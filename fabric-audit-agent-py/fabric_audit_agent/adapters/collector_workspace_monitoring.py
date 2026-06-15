"""User-attribution CollectorPort — Fabric Workspace Monitoring (KQL Eventhouse).

Queries the workspace's monitoring Eventhouse (semantic-model operation logs) for per-item,
per-user engine CPU and turns it into the ``items[]`` attribution the concentration detector
reads (``topUsers``, ``userCount``). Real-time, workspace-level permission (Contributor) — no
tenant admin.

CPU time is a **proxy** for CU (different unit + AS-only scope), so it ranks the driving users
correctly but is not the authoritative CU share — that comes from Capacity Metrics and wins on
merge. ``query`` is injected: ``query(kql) -> list[dict]`` rows (unit-testable offline). The
default KQL aggregates CpuTimeMs by item + ExecutingUser; verify table/column names per tenant.
"""

_DEFAULT_KQL = (
    "SemanticModelLogs\n"
    "| where Timestamp > ago({window})\n"
    "| where isnotempty(ExecutingUser)\n"
    "| summarize cpuMs=sum(CpuTimeMs) by Workspace=WorkspaceName, Item=ArtifactName, ExecutingUser"
)


def _row(r, *names):
    for n in names:
        if r.get(n) is not None:
            return r[n]
    return None


def create_workspace_monitoring_collector(query, config=None):
    cfg = config or {}
    kql = cfg.get("kql") or _DEFAULT_KQL.format(window=cfg.get("window", "1d"))
    top_n = cfg.get("topUsers", 3)

    def collect():
        rows = query(kql) or []
        groups = {}
        for r in rows:
            name = _row(r, "Item", "item", "name", "ArtifactName")
            if not name:
                continue
            ws = _row(r, "Workspace", "workspace", "WorkspaceName") or ""
            user = _row(r, "ExecutingUser", "user")
            cpu = _row(r, "cpuMs", "CpuTimeMs", "cuSeconds") or 0
            g = groups.setdefault((ws.lower(), str(name).lower()), {"workspace": ws, "name": name, "users": {}, "cpu": 0})
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
