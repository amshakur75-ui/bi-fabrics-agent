"""User-attribution CollectorPort — Fabric Workspace Monitoring (KQL Eventhouse).

Queries the workspace's monitoring Eventhouse (``SemanticModelLogs``) for per-item, per-user
engine time and feeds the rows through the shared ``attribution_rollup`` so it emits the SAME shape
as the Log Analytics collector — ``items[]`` (the item ``concentration`` detector's input) AND
``users[]`` (the per-user ``user_concentration`` detector's input). Real-time, workspace-level
permission (Contributor) — no tenant admin.

CPU/duration time is a **proxy** for CU (different unit + AS-only scope): it ranks the driving users
correctly but is not the authoritative CU share — that comes from Capacity Metrics / Capacity Events
and wins on merge. ``query`` is injected: ``query(kql) -> list[dict]`` rows (unit-testable offline).

Default KQL is **schema-tolerant** (uses ``column_ifexists`` and resolves the user from either
``ExecutingUser`` or the structured ``Identity`` field, and the cost from ``CpuTimeMs`` or
``DurationMs``) so it runs against a fresh monitoring Eventhouse without an override. If your table
differs, set ``FABRIC_KUSTO_KQL`` (the ``kql`` config) to override the whole query.
"""
from .attribution_rollup import rollup_attribution

_DEFAULT_KQL = (
    "SemanticModelLogs\n"
    "| where Timestamp > ago({window})\n"
    "| extend _user = tostring(column_ifexists('ExecutingUser', ''))\n"
    "| extend _id = column_ifexists('Identity', dynamic(null))\n"
    "| extend ExecutingUser = iff(_user != '', _user, tostring(coalesce(_id.Email, _id.email, _id)))\n"
    "| where isnotempty(ExecutingUser)\n"
    "| extend cpuMs = coalesce(toreal(column_ifexists('CpuTimeMs', real(null))), toreal(column_ifexists('DurationMs', real(0))))\n"
    "| summarize cpuMs=sum(cpuMs) by "
    "Workspace=tostring(column_ifexists('WorkspaceName','')), "
    "Item=tostring(coalesce(column_ifexists('ItemName',''), column_ifexists('ArtifactName',''))), "
    "ExecutingUser"
)


def create_workspace_monitoring_collector(query, config=None):
    cfg = config or {}
    kql = cfg.get("kql") or _DEFAULT_KQL.format(window=cfg.get("window", "1d"))
    top_n = cfg.get("topUsers", 3)

    def collect():
        return rollup_attribution(query(kql) or [], top_n=top_n, ws_label=cfg.get("workspace", ""))

    return {"collect": collect}
