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
    assert "| take 5000" in seen["kql"]   # default cap


def test_kql_orders_by_recency_before_take():
    """KQL `take` without an order is non-deterministic — an arbitrary slice would make the
    spike tools sample randomly and miss the real spikes. The cap must keep the NEWEST events."""
    kql = _kql("1d", None, None, 5000)
    order_pos = kql.find("| order by TimeGenerated desc")
    take_pos = kql.find("| take 5000")
    assert order_pos != -1, "missing order by TimeGenerated desc"
    assert take_pos != -1
    assert order_pos < take_pos   # order must precede take to be meaningful


def test_kql_scopes_to_user_and_item_when_given():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    create_event_collector(capture, {"user": "alice@co", "item": "Sales", "cap": 100})["collect"]()
    assert 'ExecutingUser =~ "alice@co"' in seen["kql"]
    assert 'ArtifactName =~ "Sales"' in seen["kql"]
    assert "| take 100" in seen["kql"]


def test_kql_strips_quotes_from_user_and_item_to_avoid_injection():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    create_event_collector(capture, {"user": 'a"; drop table x', "item": 'b"c'})["collect"]()
    assert 'ExecutingUser =~ "a; drop table x"' in seen["kql"]
    assert 'ArtifactName =~ "bc"' in seen["kql"]


def test_kql_strips_backslashes_too():
    """A trailing backslash would escape the closing quote and break the query string."""
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    create_event_collector(capture, {"user": "trailing\\", "item": "mid\\dle"})["collect"]()
    assert 'ExecutingUser =~ "trailing"' in seen["kql"]
    assert 'ArtifactName =~ "middle"' in seen["kql"]


def test_kql_override_bypasses_builder():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    create_event_collector(capture, {"kql": "CustomTable | take 1"})["collect"]()
    assert seen["kql"] == "CustomTable | take 1"


def test_window_default_and_override():
    default_kql = _kql("1d", None, None, 5000)
    assert "ago(1d)" in default_kql
    custom_kql = _kql("7d", None, None, 5000)
    assert "ago(7d)" in custom_kql
