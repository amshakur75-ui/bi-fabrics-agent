"""Result envelope + char-budget row limiter — johnib/kusto-mcp-derived binary-search cap.
Pure stdlib, deterministic (no clock)."""
import json

from fabric_audit_agent.query.envelope import cap_rows, finish


# ---------------------------------------------------------------------------
# cap_rows
# ---------------------------------------------------------------------------

def test_cap_rows_small_records_unchanged():
    records = [{"a": 1}, {"b": 2}, {"c": 3}]
    rows, meta = cap_rows(records, max_chars=12000)
    assert rows == records
    assert rows is not records or True  # no requirement on identity, only equality
    assert meta["truncated"] is False
    assert meta["rowCount"] == 3
    assert meta["originalRowCount"] == 3
    assert meta["responseChars"] == len(json.dumps(records, default=str))
    assert meta["capMode"] == "charBudget"


def test_cap_rows_fat_records_are_binary_search_capped():
    # 100 "fat" rows -- each row's JSON serialization is long enough that a small max_chars
    # budget cannot hold all 100.
    records = [{"id": i, "blob": "x" * 500} for i in range(100)]
    rows, meta = cap_rows(records, max_chars=2000)
    assert meta["truncated"] is True
    assert meta["originalRowCount"] == 100
    assert len(rows) == meta["rowCount"]
    assert meta["rowCount"] < 100
    assert rows == records[: meta["rowCount"]]
    assert meta["capMode"] == "charBudget"
    # The kept slice must actually fit the budget...
    assert len(json.dumps(rows, default=str)) <= 2000
    # ...and it must be the LARGEST such k (binary search correctness): one more row overflows.
    if meta["rowCount"] < len(records):
        assert len(json.dumps(records[: meta["rowCount"] + 1], default=str)) > 2000
    assert meta["responseChars"] == len(json.dumps(rows, default=str))


def test_cap_rows_min_rows_floor_when_even_one_row_exceeds_budget():
    records = [{"id": i, "blob": "y" * 5000} for i in range(10)]
    rows, meta = cap_rows(records, max_chars=100, min_rows=1)
    assert meta["truncated"] is True
    assert len(rows) == 1
    assert rows == records[:1]
    assert meta["rowCount"] == 1
    assert meta["originalRowCount"] == 10


def test_cap_rows_min_rows_respected_above_one():
    records = [{"id": i, "blob": "z" * 5000} for i in range(10)]
    rows, meta = cap_rows(records, max_chars=100, min_rows=3)
    assert len(rows) == 3
    assert meta["rowCount"] == 3
    assert meta["truncated"] is True


def test_cap_rows_empty_list():
    rows, meta = cap_rows([], max_chars=12000)
    assert rows == []
    assert meta["truncated"] is False
    assert meta["rowCount"] == 0
    assert meta["originalRowCount"] == 0


def test_cap_rows_uses_default_for_non_json_native_values():
    # A value json.dumps can't natively serialize (e.g. a set) must not crash the size probe --
    # cap_rows uses default=str.
    records = [{"id": i, "tags": {1, 2, 3}} for i in range(5)]
    rows, meta = cap_rows(records, max_chars=12000)
    assert meta["truncated"] is False
    assert len(rows) == 5


def test_cap_rows_exact_boundary_not_truncated():
    # A payload whose serialized length is EXACTLY max_chars must not be truncated (<=, not <).
    records = [{"a": 1}]
    exact_len = len(json.dumps(records, default=str))
    rows, meta = cap_rows(records, max_chars=exact_len)
    assert meta["truncated"] is False
    assert meta["rowCount"] == 1


# ---------------------------------------------------------------------------
# finish
# ---------------------------------------------------------------------------

def test_finish_adds_row_count_and_none_kql_by_default():
    payload = {"events": [{"a": 1}, {"b": 2}], "source": "mock"}
    out = finish(payload, rows_key="events")
    assert out["rowCount"] == 2
    assert out["queryKql"] is None
    assert out["source"] == "mock"
    assert out["events"] == payload["events"]


def test_finish_passes_through_kql():
    payload = {"events": []}
    out = finish(payload, rows_key="events", kql="Events | take 10")
    assert out["queryKql"] == "Events | take 10"


def test_finish_merges_extra_without_mutating_input():
    payload = {"spikes": [{"x": 1}], "topItems": ["a", "b"]}
    original = dict(payload)
    out = finish(payload, rows_key="spikes", extra={"windowLabel": "30d", "queryStats": {"tookMs": 12}})
    assert out["windowLabel"] == "30d"
    assert out["queryStats"] == {"tookMs": 12}
    assert out["rowCount"] == 1
    assert out["topItems"] == ["a", "b"]
    # payload itself is untouched
    assert payload == original
    assert "windowLabel" not in payload


def test_finish_returns_new_dict_not_same_object():
    payload = {"events": []}
    out = finish(payload, rows_key="events")
    assert out is not payload


def test_finish_extra_can_override_only_via_explicit_keys_not_rows_key():
    # extra merging happens in addition to rowCount/queryKql; sanity check ordering doesn't
    # clobber the rows_key list itself.
    payload = {"events": [1, 2, 3]}
    out = finish(payload, rows_key="events", extra={"note": "ok"})
    assert out["events"] == [1, 2, 3]
    assert out["note"] == "ok"
