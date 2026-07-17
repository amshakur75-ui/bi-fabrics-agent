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
from ..query.kql_guard import escape_string, first_statement


# Top-level operation events (the complete query / refresh). VertiPaq storage-engine sub-query
# events (``VertiPaqSEQueryEnd``, ...) are CHILDREN of a ``QueryEnd`` — summing them double-counts
# cost and surfaces meaningless "spikes" (a raw SE scan). A deployer can restrict to top-level ops
# via ``config["operations"]`` AFTER confirming the live OperationName values (getschema). Default
# is unfiltered so this can never return empty on a tenant whose op names differ.
_RECOMMENDED_TOP_LEVEL_OPS = ("QueryEnd", "CommandEnd", "ProgressReportEnd")


def _kql(window, user, item, cap, operations=None, order="cost", exclude_prefixes=None):
    """``window`` is a full KQL WHERE-clause string (e.g. ``"| where TimeGenerated > ago(1d)"``
    or a ``between (...)`` clause), as built by ``query.windows.resolve_window`` -- NOT a bare
    lookback like ``"1d"``. Spliced in verbatim as its own line. Absolute windows (spike
    investigation around a named moment, where a relative ago() lookback + row cap could truncate
    the very slice asked about) are produced by ``resolve_window(start=, end=)`` as a
    ``between (...)`` clause and passed in AS ``window`` -- so this needs no start/end params.

    ``operations`` = OperationName ALLOWLIST (``in (...)``). ``exclude_prefixes`` = a DENYLIST of
    OperationName prefixes to drop (e.g. ``["VertiPaqSE"]`` -- the storage-engine sub-query children
    that double-count a QueryEnd). Use the denylist when the caller wants EVERY top-level op type
    (QueryEnd/CommandEnd/DiscoverEnd + XMLA reads + anything else) minus only the SE noise, instead
    of a fixed allowlist that silently hides op types like XMLA Read Operations."""
    lines = ["PowerBIDatasetsWorkspace",
             window,
             "| where isnotempty(ExecutingUser)"]
    if user:
        # Match a full UPN exactly OR a short display name against the local part
        # (bryant.carlson -> bryant.carlson@newellco.com), so a scoped pull never misses a user
        # just because the caller passed the short name the UI shows. =~ is case-insensitive.
        u = escape_string(user)
        lines.append('| where ExecutingUser =~ "{0}" or ExecutingUser startswith "{0}@"'.format(u))
    if item:
        lines.append('| where ArtifactName =~ "{}"'.format(escape_string(item)))
    if operations:
        ops = ", ".join('"{}"'.format(escape_string(o)) for o in operations)
        lines.append(f"| where OperationName in ({ops})")
    for pref in (exclude_prefixes or []):
        lines.append('| where not(OperationName startswith "{}")'.format(escape_string(pref)))
    lines.append("| project TimeGenerated, ExecutingUser, ArtifactName, PowerBIWorkspaceName, "
                 "OperationName, OperationDetailName, CpuTimeMs, DurationMs, EventText")
    # Deterministic + complete-for-cost: a bare ``take`` returns an ARBITRARY, non-repeatable subset
    # (results shift between calls and the true peak can be missed entirely). ``top ... by cost``
    # keeps the most expensive events under the cap. ``order="recent"`` keeps newest-first instead
    # (use for time-bucketed analysis that needs contiguous coverage, e.g. capacity_patterns).
    sort_key = "TimeGenerated" if order == "recent" else "coalesce(CpuTimeMs, DurationMs)"
    lines.append(f"| top {int(cap)} by {sort_key} desc")
    return "\n".join(lines)


def create_event_collector(query, config=None):
    """Build a CollectorPort returning ``{"collect": () -> list[normalized event dict], "kql":
    <the built/effective query string>}``. The ``kql`` key lets a caller (e.g. ``tools.py``'s
    ``_events_or_mock``) surface the exact query that will run as ``queryKql`` on a tool envelope,
    without re-deriving it.

    ``config`` keys: ``window`` (a full KQL WHERE-clause string, e.g. the ``clause`` from
    ``query.windows.resolve_window`` -- default ``"| where TimeGenerated > ago(1d)"``; an absolute
    window is produced by ``resolve_window(start=, end=)`` as a ``between (...)`` clause and passed
    in here AS ``window``), ``user`` (scope to one ExecutingUser), ``item`` (scope to one
    ArtifactName), ``cap`` (row cap, default 5000), ``operations`` (optional OperationName allowlist
    — recommend ``_RECOMMENDED_TOP_LEVEL_OPS`` after verifying live op names, to drop VertiPaq SE
    sub-query events that double-count), ``order`` ("cost" [default] keeps the most expensive events
    under the cap; "recent" keeps the newest), ``kql`` (override the whole query).
    """
    cfg = config or {}
    built = _kql(
        cfg.get("window", "| where TimeGenerated > ago(1d)"), cfg.get("user"), cfg.get("item"),
        cfg.get("cap", 5000), cfg.get("operations"), cfg.get("order", "cost"),
        cfg.get("excludePrefixes"),
    )
    # A cfg["kql"] override is trusted (e.g. FABRIC_CAPACITY_EVENTS_KQL, a multi-line/`let`
    # flatten) and passed through UNMODIFIED -- first_statement() would wrongly truncate it.
    # A BUILT query, however, is guarded: truncate at the first top-level `;` in case an
    # unescaped/unquoted interpolation seam (e.g. `window`) lets one slip through.
    kql = cfg.get("kql") or first_statement(built)

    def collect():
        return [normalize_event(r) for r in (query(kql) or [])]

    return {"collect": collect, "kql": kql}
