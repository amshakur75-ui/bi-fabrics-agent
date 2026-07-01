"""Raw per-event CollectorPort — Log Analytics PowerBIDatasetsWorkspace.

Unlike ``collector_log_analytics`` (which SUMMARIZES per (workspace,item,user)), this returns one
normalized event per query row so the Phase-3 tools (``user_spike_history``, ``spike_events``,
``capacity_patterns``) see event-level depth. ``query(kql) -> list[dict]`` is injected (swaps to
``adapters.clients.build_log_analytics_query`` at deploy; scope
``https://api.loganalytics.io/.default``, Azure RBAC "Log Analytics Reader"; read-only).

Schema status: the table ``PowerBIDatasetsWorkspace`` and the columns ``TimeGenerated``,
``ExecutingUser``, ``ArtifactName``, ``PowerBIWorkspaceName``, ``CpuTimeMs`` are already confirmed
live (the sibling ``collector_log_analytics`` deploys against them today). ``OperationName`` and
``EventText`` are new for Phase 3 and have **not** been independently verified against this
tenant's live schema — run ``getschema`` (or a small ``take 5`` sample) against
``PowerBIDatasetsWorkspace`` before pointing this collector at production, and confirm
``OperationName`` carries ``QueryEnd`` (interactive) and ``CommandEnd``/``ProgressReportEnd``
(refresh) per the Power BI Log Analytics diagnostic-logging reference. ``normalize_event``'s keys
are tolerant, so a mismatch here is a silent-miss risk (wrong ``kind``/empty ``queryText``), not a
crash — verify first.
"""
from ..investigation.events import normalize_event


def _kql(window, user, item, cap):
    lines = ["PowerBIDatasetsWorkspace",
             f"| where TimeGenerated > ago({window})",
             "| where isnotempty(ExecutingUser)"]
    if user:
        lines.append('| where ExecutingUser =~ "{}"'.format(user.replace('"', "")))
    if item:
        lines.append('| where ArtifactName =~ "{}"'.format(item.replace('"', "")))
    lines.append("| project TimeGenerated, ExecutingUser, ArtifactName, PowerBIWorkspaceName, "
                 "OperationName, CpuTimeMs, DurationMs, EventText")
    lines.append(f"| take {int(cap)}")
    return "\n".join(lines)


def create_event_collector(query, config=None):
    """Build a CollectorPort returning ``{"collect": () -> list[normalized event dict]}``.

    ``config`` keys: ``window`` (KQL lookback, default "1d"), ``user`` (scope to one
    ExecutingUser), ``item`` (scope to one ArtifactName), ``cap`` (row cap, default 5000),
    ``kql`` (override the whole query).
    """
    cfg = config or {}
    kql = cfg.get("kql") or _kql(cfg.get("window", "1d"), cfg.get("user"),
                                 cfg.get("item"), cfg.get("cap", 5000))

    def collect():
        return [normalize_event(r) for r in (query(kql) or [])]

    return {"collect": collect}
