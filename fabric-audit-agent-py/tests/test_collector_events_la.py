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
    # `window` is interpolated unescaped/unquoted into `ago(...)`, so a top-level `;` injected
    # there is a realistic defense-in-depth case for first_statement() to catch on a BUILT query
    # (escape_string already prevents breakout via the quoted user/item literals -- this covers
    # the unquoted seam). The BUILT query must be truncated before the injected `; second`.
    create_event_collector(capture, {"window": "1d; second"})["collect"]()
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


def test_window_default_and_override():
    default_kql = _kql("1d", None, None, 5000)
    assert "ago(1d)" in default_kql
    custom_kql = _kql("7d", None, None, 5000)
    assert "ago(7d)" in custom_kql


def test_order_recent_sorts_by_time_not_cost():
    kql = _kql("1d", None, None, 5000, order="recent")
    assert "top 5000 by TimeGenerated desc" in kql
    assert "coalesce(CpuTimeMs, DurationMs)" not in kql


def test_operations_allowlist_filters_when_given_but_not_by_default():
    # Default: no OperationName filter (so a differently-named tenant never returns empty).
    assert "OperationName in (" not in _kql("1d", None, None, 5000)
    # When set: restrict to the allowlist (drops VertiPaq SE sub-query events).
    kql = _kql("1d", None, None, 5000, operations=("QueryEnd", "CommandEnd", "ProgressReportEnd"))
    assert 'OperationName in ("QueryEnd", "CommandEnd", "ProgressReportEnd")' in kql
