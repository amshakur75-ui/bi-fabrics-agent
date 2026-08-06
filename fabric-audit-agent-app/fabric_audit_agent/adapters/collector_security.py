"""Security / access CollectorPort — Power BI Activity Events -> ``facts["access"]`` (FIX C).

Feeds ``detectors/security.py`` (external shares, admin grants, unusual access). The Activity Events
admin API is confirmed reachable with the current SP grants (~thousands of events/hour). HTTP client
injected (``get_json``) -> unit-testable offline. Read-only. Fail-open.

Coverage of the three ``facts["access"]`` families:
- **externalShares** — RELIABLE: ``ShareReport`` / ``ShareDashboard`` events carry
  ``SharingInformation`` (a documented list of recipients). A recipient is external when its email
  domain isn't in ``orgDomains`` (or, if that's unset, differs from the sharer's own domain).
- **adminGrants** — BEST-EFFORT: workspace-user add/update events. The exact field carrying the
  grantee + role varies by tenant/operation, so this is extracted defensively and ``sensitive`` is
  only set for workspaces named in ``sensitiveWorkspaces`` config — so it never false-flags. Confirm
  the grant field names against a live event before relying on these.
- **accessEvents** — NOT produced here: the unusual-access ratio needs a per-user historical
  baseline (a rolling store), which is a separate follow-up. Documented gap, not silently empty.

Config: ``{start, end}`` (ISO, same UTC day, <=24h window), ``orgDomains``, ``sensitiveWorkspaces``,
``baseUrl``.
"""
from datetime import datetime, timezone

_ACTIVITY_URL = "https://api.powerbi.com/v1.0/myorg/admin/activityevents"
_SHARE_OPS = {"ShareReport", "ShareDashboard", "UpdateSharingInformation",
              "CreateShareLink", "ShareSemanticModel", "ShareKitReport"}
_GRANT_OPS = {"AddWorkspaceUsersAdminAPI", "UpdateWorkspaceUsersAdminAPI",
              "AddWorkspaceUsers", "UpdateWorkspaceUsers"}


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _domain(email):
    e = str(email or "")
    return e.split("@")[-1].lower() if "@" in e else ""


def _raw_events(http, start_iso, end_iso, base_url=None):
    """Page the Activity Events admin API over ``[start, end)`` yielding RAW entities (the mapped
    twin in collector_activity drops the grant/share detail we need here)."""
    base = base_url or _ACTIVITY_URL
    url = f"{base}?startDateTime='{start_iso}'&endDateTime='{end_iso}'"
    seen, guard = set(), 0
    while url and url not in seen and guard < 1000:
        seen.add(url)
        guard += 1
        page = http.get_json(url)
        if not isinstance(page, dict):
            break
        for ent in (page.get("activityEventEntities") or []):
            yield ent
        url = page.get("continuationUri")


def create_security_collector(http, config):
    cfg = config or {}
    org_domains = {d.lower() for d in (cfg.get("orgDomains") or [])}
    sensitive_ws = {w.lower() for w in (cfg.get("sensitiveWorkspaces") or [])}
    start, end, base = cfg.get("start"), cfg.get("end"), cfg.get("baseUrl")

    def _is_external(recipient_domain, sharer_domain):
        if not recipient_domain:
            return False
        if org_domains:
            return recipient_domain not in org_domains
        return recipient_domain != sharer_domain  # heuristic when no org allowlist is configured

    def collect():
        access = {"adminGrants": [], "externalShares": [], "accessEvents": []}
        try:
            for ent in _raw_events(http, start, end, base):
                op = str(ent.get("Operation") or "")
                if op in _SHARE_OPS:
                    sharer_dom = _domain(ent.get("UserId"))
                    item = (ent.get("ArtifactName") or ent.get("ReportName")
                            or ent.get("DashboardName") or ent.get("DatasetName"))
                    for rec in (ent.get("SharingInformation") or []):
                        email = rec.get("RecipientEmail") or rec.get("UserPrincipalName")
                        if email and _is_external(_domain(email), sharer_dom):
                            access["externalShares"].append({
                                "workspace": ent.get("WorkspaceName"), "item": item,
                                "at": ent.get("CreationTime"), "sharedWith": email})
                elif op in _GRANT_OPS:
                    ws = ent.get("WorkspaceName")
                    access["adminGrants"].append({
                        "workspace": ws, "grantedAt": ent.get("CreationTime"),
                        "principal": (ent.get("TargetUserOrGroupName") or ent.get("ObjectId")
                                      or ent.get("UserId")),
                        "role": ent.get("OrgAppPermission") or ent.get("Role") or "Member",
                        "sensitive": str(ws or "").lower() in sensitive_ws})
        except Exception as exc:
            print(f"[security] access collect failed ({type(exc).__name__}: {exc})")
        return {"access": access, "collectedAt": _now_iso()}

    return {"collect": collect}
