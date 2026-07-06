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


# Top-level operation events (the complete query / refresh). VertiPaq storage-engine sub-query
# events (``VertiPaqSEQueryEnd``, ...) are CHILDREN of a ``QueryEnd`` — summing them double-counts
# cost and surfaces meaningless "spikes" (a raw SE scan). A deployer can restrict to top-level ops
# via ``config["operations"]`` AFTER confirming the live OperationName values (getschema). Default
# is unfiltered so this can never return empty on a tenant whose op names differ.
_RECOMMENDED_TOP_LEVEL_OPS = ("QueryEnd", "CommandEnd", "ProgressReportEnd")


def _quote(value):
    """Sanitize a literal for interpolation into a KQL string: strip quotes AND backslashes
    (a trailing backslash would escape the closing quote and break the query)."""
    return str(value).replace("\\", "").replace('"', "")


def _kql(window, user, item, cap, operations=None, order="cost", start=None, end=None):
    # start/end (ISO UTC) bound the query to an ABSOLUTE window — used for spike investigation
    # around a named moment, where a relative ago() lookback + row cap could truncate the very
    # slice being asked about on a busy estate. Relative ago(window) otherwise.
    if start and end:
        time_filter = f"| where TimeGenerated between (datetime({_quote(start)}) .. datetime({_quote(end)}))"
    else:
        time_filter = f"| where TimeGenerated > ago({window})"
    lines = ["PowerBIDatasetsWorkspace",
             time_filter,
             "| where isnotempty(ExecutingUser)"]
    if user:
        lines.append(f'| where ExecutingUser =~ "{_quote(user)}"')
    if item:
        lines.append(f'| where ArtifactName =~ "{_quote(item)}"')
    if operations:
        ops = ", ".join(f'"{_quote(o)}"' for o in operations)
        lines.append(f"| where OperationName in ({ops})")
    lines.append("| project TimeGenerated, ExecutingUser, ArtifactName, PowerBIWorkspaceName, "
                 "OperationName, CpuTimeMs, DurationMs, EventText")
    # Deterministic + complete-for-cost: a bare ``take`` returns an ARBITRARY, non-repeatable subset
    # (results shift between calls and the true peak can be missed entirely). ``top ... by cost``
    # keeps the most expensive events under the cap. ``order="recent"`` keeps newest-first instead
    # (use for time-bucketed analysis that needs contiguous coverage, e.g. capacity_patterns).
    sort_key = "TimeGenerated" if order == "recent" else "coalesce(CpuTimeMs, DurationMs)"
    lines.append(f"| top {int(cap)} by {sort_key} desc")
    return "\n".join(lines)


def create_event_collector(query, config=None):
    """Build a CollectorPort returning ``{"collect": () -> list[normalized event dict]}``.

    ``config`` keys: ``window`` (KQL lookback, default "1d"), ``user`` (scope to one
    ExecutingUser), ``item`` (scope to one ArtifactName), ``cap`` (row cap, default 5000),
    ``operations`` (optional OperationName allowlist — recommend ``_RECOMMENDED_TOP_LEVEL_OPS``
    after verifying live op names, to drop VertiPaq SE sub-query events that double-count),
    ``order`` ("cost" [default] keeps the most expensive events under the cap; "recent" keeps the
    newest), ``start``/``end`` (ISO UTC pair — absolute window instead of the relative lookback),
    ``kql`` (override the whole query).
    """
    cfg = config or {}
    kql = cfg.get("kql") or _kql(
        cfg.get("window", "1d"), cfg.get("user"), cfg.get("item"),
        cfg.get("cap", 5000), cfg.get("operations"), cfg.get("order", "cost"),
        cfg.get("start"), cfg.get("end"),
    )

    def collect():
        return [normalize_event(r) for r in (query(kql) or [])]

    return {"collect": collect}
