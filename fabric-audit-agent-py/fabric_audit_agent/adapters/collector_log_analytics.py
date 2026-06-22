"""User-attribution CollectorPort — Azure Log Analytics (Power BI semantic-model logs).

The Log Analytics twin of ``collector_workspace_monitoring``: same output shape (an ``items[]``
list the concentration detector reads — ``topUsers``, ``userCount``, ``sharePct``), but the data
comes from the Azure Monitor **Logs query API** (``api.loganalytics.io``) instead of a Fabric
Eventhouse. Use this on workspaces wired to Log Analytics (e.g. a capacity-sensitive prod
workspace where the Monitoring Eventhouse's CU cost is undesirable); use the Eventhouse collector
on workspaces where Workspace Monitoring is enabled. One source per workspace — Fabric forbids both
on the same workspace.

CPU time is a **proxy** for CU (engine CPU, AS-only scope), so it ranks the driving users correctly
but is not the authoritative capacity CU share — that comes from Capacity Metrics / Capacity Events
and wins on merge.

``query`` is injected: ``query(kql) -> list[dict]`` rows (unit-testable offline; swaps to
``adapters.clients.build_log_analytics_query`` at deploy, which authenticates with the
``https://api.loganalytics.io/.default`` scope — NOT the ARM scope — and an Azure RBAC
``Log Analytics Reader`` role on the workspace).

Schema note: Log Analytics names the item ``ArtifactName`` (the Eventhouse uses ``ItemName``); the
table is ``PowerBIDatasetsWorkspace``. ``_row`` resolves either spelling so the same downstream code
(attribution, the 30% detector) stays source-agnostic. Verify column names with ``getschema`` at
deploy.
"""

# PowerBIDatasetsWorkspace is the LA table for Power BI semantic-model engine logs. PowerBIWorkspaceName
# carries the real workspace name (confirmed in the live schema), so we group by it for accurate
# (workspace, item) merge keys; the config "workspace" label is only a fallback if a tenant's table omits it.
_DEFAULT_KQL = (
    "PowerBIDatasetsWorkspace\n"
    "| where TimeGenerated > ago({window})\n"
    "| where isnotempty(ExecutingUser)\n"
    "| summarize cpuMs=sum(CpuTimeMs) by PowerBIWorkspaceName, ArtifactName, ExecutingUser"
)


def _row(r, *names):
    """First key present and non-None — tolerant of LA (ArtifactName) vs Eventhouse (ItemName)."""
    for n in names:
        if r.get(n) is not None:
            return r[n]
    return None


def create_log_analytics_collector(query, config=None):
    """Build a CollectorPort that returns per-item user attribution from Log Analytics.

    ``config`` keys: ``window`` (KQL lookback, default "1d"), ``kql`` (override the query),
    ``topUsers`` (rank cutoff, default 3), ``workspace`` (label stamped on items when the LA rows
    carry no workspace column).
    """
    cfg = config or {}
    kql = cfg.get("kql") or _DEFAULT_KQL.format(window=cfg.get("window", "1d"))
    top_n = cfg.get("topUsers", 3)
    ws_label = cfg.get("workspace") or ""

    def collect():
        rows = query(kql) or []
        groups = {}
        for r in rows:
            name = _row(r, "Item", "item", "name", "ItemName", "ArtifactName")
            if not name:
                continue
            ws = _row(r, "Workspace", "workspace", "WorkspaceName", "PowerBIWorkspaceName") or ws_label
            user = _row(r, "ExecutingUser", "user")
            cpu = _row(r, "cpuMs", "CpuTimeMs", "cuSeconds") or 0
            g = groups.setdefault((ws.lower(), str(name).lower()),
                                  {"workspace": ws, "name": name, "users": {}, "cpu": 0})
            g["cpu"] += cpu
            if user:
                g["users"][user] = g["users"].get(user, 0) + cpu

        total = sum(g["cpu"] for g in groups.values())
        items = []
        for g in groups.values():
            ranked = sorted(({"user": u, "cuSeconds": c} for u, c in g["users"].items()),
                            key=lambda x: -x["cuSeconds"])
            items.append({
                "workspace": g["workspace"], "name": g["name"], "cuSeconds": g["cpu"],
                "sharePct": (g["cpu"] / total * 100) if total else 0,
                "topUsers": ranked[:top_n], "userCount": len(ranked), "attributionMode": "cost",
            })
        return {"items": items}

    return {"collect": collect}
