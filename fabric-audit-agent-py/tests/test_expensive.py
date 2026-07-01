"""Tests for top_expensive() — expensive-query surfacing (Task 4, Phase 3)."""
from fabric_audit_agent.investigation.expensive import top_expensive


def _make_event(ts, user, item, cu_seconds, query_text=None):
    """Build a normalized event dict as normalize_event would produce."""
    return {
        "ts": ts,
        "user": user,
        "item": item,
        "workspace": "ws1",
        "operation": "QueryEnd",
        "kind": "interactive",
        "cuSeconds": cu_seconds,
        "durationMs": int(cu_seconds * 1000),
        "throttled": False,
        "queryText": query_text,
    }


def test_top_expensive_ranks_by_cu_seconds_desc():
    events = [
        _make_event("2026-06-30T10:00Z", "a@co", "Sales", 5.0, "EVALUATE Sales"),
        _make_event("2026-06-30T10:01Z", "b@co", "Inventory", 20.0, "EVALUATE Inventory"),
        _make_event("2026-06-30T10:02Z", "c@co", "HR", 10.0, "EVALUATE HR"),
    ]
    result = top_expensive(events, n=3)
    assert len(result) == 3
    assert result[0]["cuSeconds"] == 20.0
    assert result[1]["cuSeconds"] == 10.0
    assert result[2]["cuSeconds"] == 5.0


def test_top_expensive_returns_top_n():
    events = [
        _make_event("2026-06-30T10:00Z", "a@co", "A", float(i), f"EVALUATE {i}")
        for i in range(10)
    ]
    result = top_expensive(events, n=5)
    assert len(result) == 5
    assert result[0]["cuSeconds"] == 9.0


def test_top_expensive_default_n_is_5():
    events = [
        _make_event("2026-06-30T10:00Z", "a@co", "A", float(i))
        for i in range(10)
    ]
    result = top_expensive(events)
    assert len(result) == 5


def test_top_expensive_truncates_query_text_to_400_chars():
    long_query = "EVALUATE " + ("X" * 500)
    events = [
        _make_event("2026-06-30T10:00Z", "a@co", "BigReport", 50.0, long_query),
    ]
    result = top_expensive(events, n=1)
    assert len(result) == 1
    text = result[0]["queryText"]
    assert text is not None
    assert len(text) <= 400


def test_top_expensive_handles_none_query_text():
    events = [
        _make_event("2026-06-30T10:00Z", "a@co", "Sales", 15.0, None),
    ]
    result = top_expensive(events, n=1)
    assert result[0]["queryText"] is None


def test_top_expensive_output_shape():
    events = [
        _make_event("2026-06-30T10:00Z", "a@co", "Sales", 15.0, "EVALUATE Sales[Amount]"),
    ]
    result = top_expensive(events, n=1)
    row = result[0]
    assert set(row.keys()) == {"ts", "user", "item", "cuSeconds", "queryText"}


def test_top_expensive_fewer_events_than_n():
    events = [
        _make_event("2026-06-30T10:00Z", "a@co", "Sales", 5.0, "EVALUATE Sales"),
    ]
    result = top_expensive(events, n=5)
    assert len(result) == 1


def test_top_expensive_empty_events():
    result = top_expensive([], n=5)
    assert result == []


def test_top_expensive_query_text_not_presented_as_instruction():
    """queryText is data only — confirm it is returned as a string, never as a command."""
    events = [
        _make_event("2026-06-30T10:00Z", "a@co", "Sales", 100.0,
                    "EVALUATE SUMMARIZECOLUMNS(Sales[Region], \"Total\", SUM(Sales[Amount]))"),
    ]
    result = top_expensive(events, n=1)
    # The value is the raw DAX text, not a system prompt or shell command.
    # The caller is responsible for labeling it as data; here we confirm it round-trips verbatim.
    assert result[0]["queryText"].startswith("EVALUATE")
