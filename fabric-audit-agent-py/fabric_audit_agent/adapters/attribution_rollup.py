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
Metrics / Capacity Events and wins on merge.
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
    """rows -> ``{"items": [...], "users": [...]}``. Pure; safe on empty/malformed input.

    N7 fix: ``attributionMode`` now distinguishes ``"cost-cpu"`` (true ``CpuTimeMs``) from
    ``"cost-duration"`` (the weaker ``DurationMs`` wall-clock fallback) rather than collapsing
    both into a single ``"cost"`` label — downstream code (and the agent's own wording) can now
    tell which proxy actually backs a given number instead of treating both as equally grounded.

    A3 fix: each item/user now carries ``truncated`` (bool) — True when more distinct
    users/items existed than ``top_n`` kept, so downstream logic and responses can say
    "showing top N of possibly more" instead of silently implying the list is complete.
    """
    groups = {}
    by_user = {}   # user -> {cpu, items{name: cpu}, hasCpuTime} — the per-user rollup (who, and via what)
    for r in rows or []:
        if not isinstance(r, dict):
            continue   # defensive: never crash on a stray non-dict row from a real query
        name = _row(r, "Item", "item", "name", "ItemName", "ArtifactName")
        if not name:
            continue
        ws = _row(r, "Workspace", "workspace", "WorkspaceName", "PowerBIWorkspaceName") or ws_label
        user = identity_email(_row(r, "ExecutingUser", "user", "Identity"))
        # Cost column is milliseconds (CpuTimeMs / DurationMs) -> convert to CU-seconds so this
        # matches ``normalize_event``'s scale (this rollup previously emitted MILLISECONDS labelled
        # as cuSeconds, ~1000x off from the event path). ``cuSeconds`` input is already seconds.
        # N7: track whether THIS row's cost came from true CpuTimeMs or the DurationMs fallback,
        # so the group-level mode can reflect what actually backed the numbers.
        cpu_time_ms = _row(r, "CpuTimeMs")
        raw_ms = cpu_time_ms if cpu_time_ms is not None else _row(r, "cpuMs", "DurationMs")
        cs = r.get("cuSeconds")
        cpu = (raw_ms / 1000.0) if raw_ms is not None else (cs if cs is not None else 0)
        is_true_cpu = cpu_time_ms is not None
        g = groups.setdefault((str(ws).lower(), str(name).lower()),
                              {"workspace": ws, "name": name, "users": {}, "cpu": 0, "hasCpuTime": False})
        g["cpu"] += cpu
        g["hasCpuTime"] = g["hasCpuTime"] or is_true_cpu
        if user:
            g["users"][user] = g["users"].get(user, 0) + cpu
            u = by_user.setdefault(user, {"user": user, "cpu": 0, "items": {}, "hasCpuTime": False})
            u["cpu"] += cpu
            u["hasCpuTime"] = u["hasCpuTime"] or is_true_cpu
            u["items"][name] = u["items"].get(name, 0) + cpu

    total = sum(g["cpu"] for g in groups.values())

    items = []
    for g in groups.values():
        ranked = sorted(({"user": u, "cuSeconds": round(c, 3)} for u, c in g["users"].items()),
                        key=lambda x: -x["cuSeconds"])
        user_count = len(ranked)
        items.append({
            "workspace": g["workspace"], "name": g["name"], "cuSeconds": round(g["cpu"], 3),
            "sharePct": (g["cpu"] / total * 100) if total else 0,
            "topUsers": ranked[:top_n], "userCount": user_count,
            "attributionMode": "cost-cpu" if g["hasCpuTime"] else "cost-duration",
            "truncated": user_count > top_n,
        })

    users = []
    for u in by_user.values():
        top_items = sorted(({"name": n, "cuSeconds": round(c, 3)} for n, c in u["items"].items()),
                           key=lambda x: -x["cuSeconds"])
        item_count = len(top_items)
        users.append({
            "user": u["user"], "cuSeconds": round(u["cpu"], 3),
            "sharePct": (u["cpu"] / total * 100) if total else 0,
            "topItems": top_items[:top_n], "itemCount": item_count,
            "attributionMode": "cost-cpu" if u["hasCpuTime"] else "cost-duration",
            "truncated": item_count > top_n,
        })
    users.sort(key=lambda x: -x["cuSeconds"])

    return {"items": items, "users": users}
