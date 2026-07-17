"""Tests for the raw per-event Log Analytics collector (offline; injected query).

Unlike collector_log_analytics (SUMMARIZE'd attribution), this collector must return one
normalized event per row -- these tests assert kind/cuSeconds/queryText survive the round trip.
"""
from fabric_audit_agent.adapters.collector_events_la import create_event_collector, _kql


def test_returns_one_normalized_event_per_row():
    rows = [
        {"TimeGenerated": "2026-06-30T09:00:00Z", "ExecutingUser": "alice@co",
         "ArtifactName": "Sales", "PowerBIWorkspaceName": "Enterprise Sales",
         "OperationName": "QueryEnd", "CpuTimeMs": 8000,
         "EventText": "EVALUATE TOPN(100, Sales, [Revenue])"},
        {"TimeGenerated": "2026-06-30T09:05:00Z", "ExecutingUser": "bob@co",
         "ArtifactName": "HR", "PowerBIWorkspaceName": "HR Workspace",
         "OperationName": "CommandEnd", "DurationMs": 20000},
    ]
    events = create_event_collector(lambda kql: rows)["collect"]()

    assert len(events) == 2
    interactive, refresh = events

    assert interactive["kind"] == "interactive"
    assert interactive["cuSeconds"] == 8.0
    assert interactive["user"] == "alice@co"
    assert interactive["item"] == "Sales"
    assert interactive["workspace"] == "Enterprise Sales"
    assert interactive["queryText"] == "EVALUATE TOPN(100, Sales, [Revenue])"

    assert refresh["kind"] == "refresh"
    assert refresh["cuSeconds"] == 20.0   # DurationMs fallback when CpuTimeMs absent
    assert refresh["queryText"] is None


def test_empty_rows_returns_empty_list():
    assert create_event_collector(lambda kql: [])["collect"]() == []


def test_kql_defaults_to_whole_estate_no_user_or_item_filter():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    create_event_collector(capture)["collect"]()
    assert 'ExecutingUser =~' not in seen["kql"]
    assert 'ArtifactName =~' not in seen["kql"]
    assert "PowerBIDatasetsWorkspace" in seen["kql"]
    # Deterministic top-by-cost (NOT a bare `take`, which returns an arbitrary, non-repeatable set).
    assert "top 5000 by coalesce(CpuTimeMs, DurationMs) desc" in seen["kql"]
    assert "| take 5000" not in seen["kql"]


def test_kql_scopes_to_user_and_item_when_given():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    create_event_collector(capture, {"user": "alice@co", "item": "Sales", "cap": 100})["collect"]()
    assert 'ExecutingUser =~ "alice@co"' in seen["kql"]
    assert 'ArtifactName =~ "Sales"' in seen["kql"]
    assert "top 100 by coalesce(CpuTimeMs, DurationMs) desc" in seen["kql"]


def test_kql_escapes_quotes_from_user_and_item_preserving_content_no_breakout():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    create_event_collector(capture, {"user": 'a"; drop | take 1', "item": 'b"c'})["collect"]()
    assert 'ExecutingUser =~ "a\\"; drop | take 1"' in seen["kql"]
    assert 'ArtifactName =~ "b\\"c"' in seen["kql"]


def test_kql_escapes_backslashes_too():
    """A trailing backslash would escape the closing quote and break the query string. The unified
    design uses kql_guard.escape_string (stricter than the dropped _quote strip): it ESCAPES the
    backslash (\\ -> \\\\) so the literal content is preserved AND the closing quote can't be
    escaped away -- no breakout."""
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    create_event_collector(capture, {"user": "trailing\\", "item": "mid\\dle"})["collect"]()
    # escape_string doubles each backslash; the closing quote stays intact (no breakout).
    assert 'ExecutingUser =~ "trailing\\\\"' in seen["kql"]
    assert 'ArtifactName =~ "mid\\\\dle"' in seen["kql"]


def test_absolute_window_replaces_relative_lookback():
    """An absolute window bounds the query in KQL itself -- the row cap can never truncate away
    the exact window a spike investigation asks about. Under the unified design the absolute
    between() clause is produced by query.windows.resolve_window(start=, end=) and passed in AS
    the ``window`` config (verbatim), NOT via start/end keys on the collector."""
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    between_clause = ("| where TimeGenerated between (datetime(2026-07-06T15:18:00Z) .. "
                      "datetime(2026-07-06T16:18:00Z))")
    create_event_collector(capture, {"window": between_clause})["collect"]()
    assert ("TimeGenerated between (datetime(2026-07-06T15:18:00Z) .. "
            "datetime(2026-07-06T16:18:00Z))") in seen["kql"]
    assert "ago(" not in seen["kql"]


def test_kql_override_bypasses_builder():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    create_event_collector(capture, {"kql": "CustomTable | take 1"})["collect"]()
    assert seen["kql"] == "CustomTable | take 1"


def test_built_kql_with_injected_semicolon_is_truncated_to_first_statement():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    # `window` is spliced in verbatim as its own line, so a top-level `;` injected into it is a
    # realistic defense-in-depth case for first_statement() to catch on a BUILT query (escape_string
    # already prevents breakout via the quoted user/item literals -- this covers the window seam).
    # The BUILT query must be truncated before the injected `; second`.
    create_event_collector(capture, {"window": "| where TimeGenerated > ago(1d); second"})["collect"]()
    assert "second" not in seen["kql"]
    assert seen["kql"] == seen["kql"].rstrip()


def test_kql_override_with_let_and_semicolon_passes_through_untouched():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    override = "let x = 1; x | take 5"
    create_event_collector(capture, {"kql": override})["collect"]()
    # The trusted override (e.g. FABRIC_CAPACITY_EVENTS_KQL) is NOT run through first_statement.
    assert seen["kql"] == override


_CLAUSE_1D = "| where TimeGenerated > ago(1d)"
_CLAUSE_7D = "| where TimeGenerated > ago(7d)"


def test_window_default_and_override():
    default_kql = _kql(_CLAUSE_1D, None, None, 5000)
    assert "ago(1d)" in default_kql
    custom_kql = _kql(_CLAUSE_7D, None, None, 5000)
    assert "ago(7d)" in custom_kql


def test_window_clause_is_spliced_in_verbatim():
    # _kql accepts a FULL WHERE-clause string (as built by query.windows.resolve_window),
    # not a bare lookback -- e.g. an absolute between() clause must pass through untouched.
    clause = "| where TimeGenerated between (datetime(2026-07-05T12:45:00Z) .. datetime(2026-07-05T13:00:00Z))"
    kql = _kql(clause, None, None, 5000)
    assert clause in kql


def test_order_recent_sorts_by_time_not_cost():
    kql = _kql(_CLAUSE_1D, None, None, 5000, order="recent")
    assert "top 5000 by TimeGenerated desc" in kql
    assert "coalesce(CpuTimeMs, DurationMs)" not in kql


def test_operations_allowlist_filters_when_given_but_not_by_default():
    # Default: no OperationName filter (so a differently-named tenant never returns empty).
    assert "OperationName in (" not in _kql(_CLAUSE_1D, None, None, 5000)
    # When set: restrict to the allowlist (drops VertiPaq SE sub-query events).
    kql = _kql(_CLAUSE_1D, None, None, 5000, operations=("QueryEnd", "CommandEnd", "ProgressReportEnd"))
    assert 'OperationName in ("QueryEnd", "CommandEnd", "ProgressReportEnd")' in kql


def test_user_filter_matches_short_name_or_full_upn():
    # A scoped pull with the SHORT display name must still catch the full UPN in the data --
    # otherwise an XMLA-read lookup for "bryant.carlson" misses bryant.carlson@newellco.com.
    kql = _kql(_CLAUSE_1D, "bryant.carlson", None, 5000)
    assert 'ExecutingUser =~ "bryant.carlson"' in kql
    assert 'ExecutingUser startswith "bryant.carlson@"' in kql


def test_exclude_prefixes_denylist_drops_se_children_shows_all_else():
    # The denylist path (capacity_peaks: all_operations) keeps EVERY op type and drops only the
    # VertiPaqSE storage-engine sub-query children that double-count -- so XMLA reads / discovers
    # / any other op survive, unlike the fixed allowlist that hid them.
    kql = _kql(_CLAUSE_1D, None, None, 5000, exclude_prefixes=["VertiPaqSE"])
    assert 'where not(OperationName startswith "VertiPaqSE")' in kql
    assert "OperationName in (" not in kql   # denylist, not allowlist
