"""Shared per-(workspace, item) + per-user attribution rollup.

Both the Log Analytics and Workspace Monitoring collectors feed their query rows through this so
they emit an identical shape — ``items[]`` (the input for the item ``concentration`` detector) and
``users[]`` (the input for the per-user ``user_concentration`` detector). Keeping one rollup means
the two sources can't drift, and it's the single place that has to be source-tolerant.

Tolerance (why this exists):
  * column spelling differs by source — Log Analytics names the item ``ArtifactName`` and the
    workspace ``PowerBIWorkspaceName``; the Fabric Workspace-Monitoring Eventhouse uses ``ItemName``
    / ``WorkspaceName`` and carries the user as a structured ``Identity`` field (not ``ExecutingUser``).
  * the cost column is ``CpuTimeMs`` when present, else ``DurationMs`` (a wall-clock proxy) — the live
    SemanticModelLogs table seen in the field did not expose ``CpuTimeMs``.
  * a real Kusto/Logs client returns a list of dict rows, but we never want a stray non-dict value
    (or a mis-shaped query result) to crash the whole audit — such rows are skipped.

CPU/duration time is a **proxy** for CU (engine time, AS-only scope): it ranks the driving users
correctly but is not the authoritative capacity CU share. That share comes from Capacity
Metrics / Capacity Events and wins on merge. ``attributionMode`` is stamped ``"cost"`` so downstream
labelling can say "monitored CU" rather than implying a true capacity share.
"""


def identity_email(value):
    """Resolve a user string from whatever the source put in the user column.

    Workspace Monitoring's ``Identity`` arrives as a structured object ({"Email": ...} /
    {"email": ...} / {"UserPrincipalName": ...}); Log Analytics' ``ExecutingUser`` is a plain
    string. Return the cleanest user handle we can, else the value unchanged."""
    if isinstance(value, dict):
        return (value.get("Email") or value.get("email")
                or value.get("UserPrincipalName") or value.get("upn") or value.get("User"))
    return value


def _row(r, *names):
    """First key present and non-None — tolerant of LA vs Eventhouse column spellings."""
    for n in names:
        if r.get(n) is not None:
            return r[n]
    return None


def rollup_attribution(rows, top_n=3, ws_label=""):
    """rows -> ``{"items": [...], "users": [...]}``. Pure; safe on empty/malformed input."""
    groups = {}
    by_user = {}   # user -> {cpu, items{name: cpu}} — the per-user rollup (who, and via what)
    for r in rows or []:
        if not isinstance(r, dict):
            continue   # defensive: never crash on a stray non-dict row from a real query
        name = _row(r, "Item", "item", "name", "ItemName", "ArtifactName")
        if not name:
            continue
        ws = _row(r, "Workspace", "workspace", "WorkspaceName", "PowerBIWorkspaceName") or ws_label
        user = identity_email(_row(r, "ExecutingUser", "user", "Identity"))
        cpu = _row(r, "cpuMs", "CpuTimeMs", "DurationMs", "cuSeconds") or 0
        g = groups.setdefault((str(ws).lower(), str(name).lower()),
                              {"workspace": ws, "name": name, "users": {}, "cpu": 0})
        g["cpu"] += cpu
        if user:
            g["users"][user] = g["users"].get(user, 0) + cpu
            u = by_user.setdefault(user, {"user": user, "cpu": 0, "items": {}})
            u["cpu"] += cpu
            u["items"][name] = u["items"].get(name, 0) + cpu

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

    users = []
    for u in by_user.values():
        top_items = sorted(({"name": n, "cuSeconds": c} for n, c in u["items"].items()),
                           key=lambda x: -x["cuSeconds"])
        users.append({
            "user": u["user"], "cuSeconds": u["cpu"],
            "sharePct": (u["cpu"] / total * 100) if total else 0,
            "topItems": top_items[:top_n], "itemCount": len(top_items),
        })
    users.sort(key=lambda x: -x["cuSeconds"])

    return {"items": items, "users": users}
