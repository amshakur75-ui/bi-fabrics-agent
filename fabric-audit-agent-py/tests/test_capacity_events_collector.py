"""Tests for the capacity-events collector (CU% / throttle from Real-Time Hub Capacity Overview Events)."""
from fabric_audit_agent.adapters.collector_capacity_events import create_capacity_events_collector, capacity_series

# FT64 -> baseCapacityUnits 64 CU/sec -> 30s budget = 64 * 1000 * 30 = 1,920,000 CU-ms.


def test_computes_peak_and_throttle_with_dedupe():
    rows = [
        {"capacityId": "cap1", "windowStartTime": "t1", "baseCapacityUnits": 64, "capacityUnitMs": 960000},   # 50%
        {"capacityId": "cap1", "windowStartTime": "t2", "baseCapacityUnits": 64, "capacityUnitMs": 2016000},  # 105%
        {"capacityId": "cap1", "windowStartTime": "t1", "baseCapacityUnits": 64, "capacityUnitMs": 960000},   # dup of t1
    ]
    cap = create_capacity_events_collector(lambda kql: rows)["collect"]()["capacity"]
    assert cap["peakCuPct"] == 105.0          # t2 window
    assert cap["peakAt"] == "t2"
    assert cap["throttleMinutes"] == 0.5      # one >=100% window * 30s
    assert cap["capacityId"] == "cap1"


def test_skips_psku_autoscale_rows():
    # No baseCapacityUnits (P-SKU autoscale / missing) -> can't compute % -> skipped -> nothing contributed.
    rows = [{"capacityId": "p", "windowStartTime": "t1", "capacityUnitMs": 5000}]
    assert create_capacity_events_collector(lambda kql: rows)["collect"]() == {}


def test_empty():
    assert create_capacity_events_collector(lambda kql: [])["collect"]() == {}


def test_reads_nested_data_envelope():
    # Live Capacity Overview Events nest fields under a ``data`` envelope; read them without an override.
    rows = [{"data": {"capacityId": "C", "windowStartTime": "t1",
                      "capacityUnitMs": 96000, "baseCapacityUnits": 2}}]   # budget 60000 -> 160%
    cap = create_capacity_events_collector(lambda kql: rows)["collect"]()["capacity"]
    assert cap["peakCuPct"] == 160.0 and cap["capacityId"] == "C"


def test_skips_nondict_rows():
    rows = ["CapacityEvents", None, {"capacityId": "C", "windowStartTime": "t", "baseCapacityUnits": 64, "capacityUnitMs": 960000}]
    cap = create_capacity_events_collector(lambda kql: rows)["collect"]()["capacity"]
    assert cap["peakCuPct"] == 50.0


# ---------------------------------------------------------------------------
# capacity_series — the full per-window series (not reduced to a single peak)
# ---------------------------------------------------------------------------

def test_series_returns_all_windows_sorted_by_ts():
    rows = [
        {"capacityId": "cap1", "windowStartTime": "t3", "baseCapacityUnits": 64, "capacityUnitMs": 960000},   # 50%
        {"capacityId": "cap1", "windowStartTime": "t1", "baseCapacityUnits": 64, "capacityUnitMs": 1920000},  # 100%
        {"capacityId": "cap1", "windowStartTime": "t2", "baseCapacityUnits": 64, "capacityUnitMs": 2016000},  # 105%
    ]
    series = capacity_series(lambda kql: rows)
    assert series == [
        {"ts": "t1", "cuPct": 100.0},
        {"ts": "t2", "cuPct": 105.0},
        {"ts": "t3", "cuPct": 50.0},
    ]


def test_series_dedupes_by_capacity_and_window():
    rows = [
        {"capacityId": "cap1", "windowStartTime": "t1", "baseCapacityUnits": 64, "capacityUnitMs": 960000},
        {"capacityId": "cap1", "windowStartTime": "t1", "baseCapacityUnits": 64, "capacityUnitMs": 960000},  # dup
    ]
    assert capacity_series(lambda kql: rows) == [{"ts": "t1", "cuPct": 50.0}]


def test_series_skips_unusable_rows():
    rows = [
        {"capacityId": "p", "windowStartTime": "t1", "capacityUnitMs": 5000},   # no baseCapacityUnits (P-SKU)
        {"capacityId": "cap1", "windowStartTime": "t2", "baseCapacityUnits": 64, "capacityUnitMs": 960000},
    ]
    assert capacity_series(lambda kql: rows) == [{"ts": "t2", "cuPct": 50.0}]


def test_series_empty():
    assert capacity_series(lambda kql: []) == []


# ---------------------------------------------------------------------------
# {window} substitution in the kql override — a hardcoded ago(...) used to
# silently defeat the threaded lookback (capacity_patterns days=7 got 1d of series).
# ---------------------------------------------------------------------------

def test_kql_override_window_placeholder_substituted_in_series():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    capacity_series(capture, {"kql": "T | where ingestion_time() > ago({window})", "window": "7d"})
    assert "ago(7d)" in seen["kql"]
    assert "{window}" not in seen["kql"]


def test_kql_override_window_placeholder_substituted_in_peak_collector():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    create_capacity_events_collector(capture, {"kql": "T | where ingestion_time() > ago({window})",
                                               "window": "3d"})["collect"]()
    assert "ago(3d)" in seen["kql"]


def test_kql_override_without_placeholder_unchanged():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    capacity_series(capture, {"kql": "T | where ingestion_time() > ago(1d)", "window": "7d"})
    assert seen["kql"] == "T | where ingestion_time() > ago(1d)"   # backward compatible


# ---------------------------------------------------------------------------
# Regression: peakAt must resolve the SAME window-timestamp field list as the
# dedupe key. A row keyed only on ``windowStart`` (not ``windowStartTime``) used
# to dedupe correctly but produce an empty peakAt, because the peak path resolved
# a narrower field list. The shared _windows() helper resolves both from one list.
# ---------------------------------------------------------------------------

def test_peak_at_resolves_windowStart_field():
    rows = [{"capacityId": "c", "windowStart": "w1",
             "baseCapacityUnits": 64, "capacityUnitMs": 2016000}]   # 105%
    cap = create_capacity_events_collector(lambda kql: rows)["collect"]()["capacity"]
    assert cap["peakCuPct"] == 105.0
    assert cap["peakAt"] == "w1"     # was "" before the _windows() unification


# ---------------------------------------------------------------------------
# kql_guard consistency (mirrors tests/test_collector_events_la.py)
# ---------------------------------------------------------------------------

def test_kql_override_with_let_and_semicolon_passes_through_untouched():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    override = "let x = 1; x | take 5"
    create_capacity_events_collector(capture, {"kql": override})["collect"]()
    # The trusted override (e.g. FABRIC_CAPACITY_EVENTS_KQL) is NOT run through first_statement.
    assert seen["kql"] == override


def test_default_kql_contains_bracket_escaped_table_name():
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    create_capacity_events_collector(capture, {"table": "CapacityEvents"})["collect"]()
    assert "['CapacityEvents']" in seen["kql"]


def test_default_kql_escapes_table_name_via_escape_entity():
    # Distinguishes escape_entity(table) from the old bare f"['{table}']" literal: a table name
    # containing a single quote must come back with the quote backslash-escaped inside the
    # brackets, proving _default_kql routes through kql_guard.escape_entity rather than
    # interpolating the raw name between literal brackets.
    seen = {}
    def capture(kql):
        seen["kql"] = kql
        return []
    create_capacity_events_collector(capture, {"table": "Cap'Events"})["collect"]()
    assert "['Cap\\'Events']" in seen["kql"]
