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

from .attribution_rollup import rollup_attribution
from ..query.kql_guard import escape_string, first_statement


# PowerBIDatasetsWorkspace is the LA table for Power BI semantic-model engine logs. PowerBIWorkspaceName
# carries the real workspace name (confirmed in the live schema), so we group by it for accurate
# (workspace, item) merge keys. A tenant-wide LA feed carries many workspaces; default is whole-estate.
# Set ``workspaceFilter`` to scope to one or more named workspaces (e.g. a single-workspace test).


def _build_default_kql(window, ws_filter):
    lines = [
        "PowerBIDatasetsWorkspace",
        f"| where TimeGenerated > ago({window})",
        "| where isnotempty(ExecutingUser)",
    ]
    if ws_filter:
        names = ", ".join('"{}"'.format(escape_string(w)) for w in ws_filter)
        lines.append(f"| where PowerBIWorkspaceName in ({names})")
    lines.append("| summarize cpuMs=sum(CpuTimeMs) by PowerBIWorkspaceName, ArtifactName, ExecutingUser")
    return "\n".join(lines)


def create_log_analytics_collector(query, config=None):
    """Build a CollectorPort that returns per-item user attribution from Log Analytics.

    ``config`` keys: ``window`` (KQL lookback, default "1d"), ``workspaceFilter`` (a workspace name
    or list/comma-string to scope to specific workspaces; default whole-estate), ``kql`` (override
    the whole query — disables the built-in filter), ``topUsers`` (rank cutoff, default 3),
    ``workspace`` (fallback label stamped on items only when the LA rows carry no workspace column).
    """
    cfg = config or {}
    ws_filter = cfg.get("workspaceFilter")
    if isinstance(ws_filter, str):
        ws_filter = [w.strip() for w in ws_filter.split(",") if w.strip()]
    built = _build_default_kql(cfg.get("window", "1d"), ws_filter)
    # Trusted cfg["kql"] override passes through unmodified; a BUILT query is guarded with
    # first_statement() as defense-in-depth against an unescaped seam (e.g. `window`).
    kql = cfg.get("kql") or first_statement(built)
    top_n = cfg.get("topUsers", 3)
    ws_label = cfg.get("workspace") or ""

    def collect():
        return rollup_attribution(query(kql) or [], top_n=top_n, ws_label=ws_label)

    return {"collect": collect}
