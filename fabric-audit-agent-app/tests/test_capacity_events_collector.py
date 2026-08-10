"""Tests for the capacity-events collector (CU% / throttle from Real-Time Hub Capacity Overview Events)."""
import pytest
from fabric_audit_agent.adapters.collector_capacity_events import (
    create_capacity_events_collector,
    capacity_series,
    capacity_burndown_chain,
)

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


# ---------------------------------------------------------------------------
# A1 — Throttle threshold signal fields (scale x100 from raw 0-1 API fraction)
# ---------------------------------------------------------------------------

def test_series_includes_threshold_signals_scaled_x100():
    """Raw API = 0-1 fraction; series must deliver values in percentage points (e.g. 123.71,
    not 1.2371) so throttle.py's max(vals) > 100.0 gate fires correctly."""
    rows = [{
        "capacityId": "c", "windowStartTime": "t1",
        "baseCapacityUnits": 64, "capacityUnitMs": 960000,
        "interactiveDelayThresholdPercentage": 1.2371,
        "interactiveRejectionThresholdPercentage": 1.0,
        "backgroundRejectionThresholdPercentage": 1.0,
    }]
    pt = capacity_series(lambda kql: rows)[0]
    assert abs(pt["interactiveDelayPct"] - 123.71) < 0.01
    assert abs(pt["interactiveRejectionPct"] - 100.0) < 0.01
    assert abs(pt["backgroundRejectionPct"] - 100.0) < 0.01


def test_series_omits_threshold_fields_when_absent():
    """No threshold fields in payload -> nothing injected into the series point."""
    rows = [{"capacityId": "c", "windowStartTime": "t1",
             "baseCapacityUnits": 64, "capacityUnitMs": 960000}]
    pt = capacity_series(lambda kql: rows)[0]
    assert "interactiveDelayPct" not in pt
    assert "interactiveRejectionPct" not in pt
    assert "backgroundRejectionPct" not in pt


def test_series_threshold_fields_accepted_via_data_envelope():
    """Fields nested under the 'data' envelope (live Capacity Overview Events format) are resolved."""
    rows = [{"data": {
        "capacityId": "c", "windowStartTime": "t1",
        "baseCapacityUnits": 64, "capacityUnitMs": 960000,
        "interactiveDelayThresholdPercentage": 1.05,
    }}]
    pt = capacity_series(lambda kql: rows)[0]
    assert abs(pt["interactiveDelayPct"] - 105.0) < 0.01


def test_threshold_fields_cannot_unblock_throttle_stage2():
    """These fields are threshold SETTINGS, not utilization -- they can never confirm throttling.
    This test asserted the opposite, which is why the misread survived its own retirement in
    tier2_check. Note production cannot even reach it: the deployed FABRIC_CAPACITY_EVENTS_KQL
    projects all three columns away, so stage 2 is unavailable there for a second reason."""
    from fabric_audit_agent.investigation.throttle import decompose_throttle
    rows = [{
        "capacityId": "c", "windowStartTime": "t1",
        "baseCapacityUnits": 64, "capacityUnitMs": 2016000,   # ~105% CU
        "interactiveDelayThresholdPercentage": 1.05,           # 105% after x100 -> gate fires
        "interactiveRejectionThresholdPercentage": 1.0,
        "backgroundRejectionThresholdPercentage": 1.0,
    }]
    series = capacity_series(lambda kql: rows)
    result = decompose_throttle(series, [])
    assert result["stage2"]["available"] is False
    assert result["stage2"]["interactiveDelay"]["fired"] is False
    assert result["conclusion"] == "over-utilized-unconfirmed"


def test_threshold_below_100_also_does_not_fire_stage2():
    """Kept as the other half of the pair: the outcome is now the same either way, because the
    signal is retired rather than merely under its bar."""
    from fabric_audit_agent.investigation.throttle import decompose_throttle
    rows = [{
        "capacityId": "c", "windowStartTime": "t1",
        "baseCapacityUnits": 64, "capacityUnitMs": 2016000,  # ~105% CU, over-utilized
        "interactiveDelayThresholdPercentage": 0.80,          # 80% after x100 -> below 100 -> not fired
        "interactiveRejectionThresholdPercentage": 0.80,
        "backgroundRejectionThresholdPercentage": 0.80,
    }]
    series = capacity_series(lambda kql: rows)
    result = decompose_throttle(series, [])
    assert result["stage2"]["interactiveDelay"]["fired"] is False
    assert result["conclusion"] == "over-utilized-unconfirmed"


# ---------------------------------------------------------------------------
# A2 — Overage / carry-forward chain fields + minutesToBurndown derivation
# ---------------------------------------------------------------------------

def test_series_includes_overage_fields():
    """All three overage fields extracted from the row and forwarded in the series point."""
    rows = [{
        "capacityId": "c", "windowStartTime": "t1",
        "baseCapacityUnits": 64, "capacityUnitMs": 2016000,
        "overageAddCapacityUnitMs": 180000,
        "overageBurndownCapacityUnitMs": -90000,
        "overageTotalCapacityUnitMs": 960000,
    }]
    pt = capacity_series(lambda kql: rows)[0]
    assert pt["overageAddMs"] == 180000
    assert pt["overageBurndownMs"] == -90000
    assert pt["overageTotalMs"] == 960000


def test_series_derives_minutes_to_burndown_from_overage_total():
    """minutesToBurndown = (overageTotal / budget * 100) / 200 -- proven exact, GAPS Section 12.3.
    F64: budget = 64*1000*30 = 1,920,000 ms.
    overageTotal = 960,000 ms -> cumulativePct = 50.0% -> minutesToBurndown = 0.25 min."""
    rows = [{
        "capacityId": "c", "windowStartTime": "t1",
        "baseCapacityUnits": 64, "capacityUnitMs": 2016000,
        "overageTotalCapacityUnitMs": 960_000,
    }]
    pt = capacity_series(lambda kql: rows)[0]
    assert abs(pt["overageCumulativePct"] - 50.0) < 0.01
    assert abs(pt["minutesToBurndown"] - 0.25) < 0.001


def test_series_omits_overage_fields_when_absent():
    rows = [{"capacityId": "c", "windowStartTime": "t1",
             "baseCapacityUnits": 64, "capacityUnitMs": 960000}]
    pt = capacity_series(lambda kql: rows)[0]
    assert "overageAddMs" not in pt
    assert "overageTotalMs" not in pt
    assert "minutesToBurndown" not in pt


def test_burndown_chain_returns_only_overage_windows():
    """Windows without overageTotalMs are excluded."""
    rows = [
        {"capacityId": "c", "windowStartTime": "t1",
         "baseCapacityUnits": 64, "capacityUnitMs": 960000},
        {"capacityId": "c", "windowStartTime": "t2",
         "baseCapacityUnits": 64, "capacityUnitMs": 2016000,
         "overageTotalCapacityUnitMs": 960_000},
        {"capacityId": "c", "windowStartTime": "t3",
         "baseCapacityUnits": 64, "capacityUnitMs": 960000},
    ]
    chain = capacity_burndown_chain(lambda kql: rows)
    assert len(chain) == 1
    assert chain[0]["ts"] == "t2"
    assert abs(chain[0]["minutesToBurndown"] - 0.25) < 0.001


def test_burndown_chain_empty_when_no_overage_windows():
    rows = [{"capacityId": "c", "windowStartTime": "t1",
             "baseCapacityUnits": 64, "capacityUnitMs": 960000}]
    assert capacity_burndown_chain(lambda kql: rows) == []


def test_burndown_chain_includes_all_fields():
    """Chain rows carry ts, cuPct, all three overage fields, cumulativePct, minutesToBurndown."""
    rows = [{
        "capacityId": "c", "windowStartTime": "t1",
        "baseCapacityUnits": 64, "capacityUnitMs": 2016000,
        "overageAddCapacityUnitMs": 200_000,
        "overageBurndownCapacityUnitMs": -100_000,
        "overageTotalCapacityUnitMs": 960_000,
    }]
    row = capacity_burndown_chain(lambda kql: rows)[0]
    for key in ("ts", "cuPct", "overageAddMs", "overageBurndownMs", "overageTotalMs",
                "overageCumulativePct", "minutesToBurndown"):
        assert key in row, f"missing key: {key}"
    assert row["overageAddMs"] == 200_000
    assert row["overageBurndownMs"] == -100_000


def test_burndown_chain_formula_f512():
    """F512 cross-check matching the real tenant from the validation session.
    budget = 512*1000*30 = 15,360,000 ms.
    overageTotal = 7,680,000 ms -> cumulativePct = 50% -> minutesToBurndown = 0.25."""
    rows = [{
        "capacityId": "c", "windowStartTime": "t1",
        "baseCapacityUnits": 512, "capacityUnitMs": 16_000_000,
        "overageTotalCapacityUnitMs": 7_680_000,
    }]
    row = capacity_burndown_chain(lambda kql: rows)[0]
    assert abs(row["overageCumulativePct"] - 50.0) < 0.01
    assert abs(row["minutesToBurndown"] - 0.25) < 0.001


def test_burndown_minutestoburndown_flows_through_series_to_throttle():
    """throttle.py reads minutesToBurndown from the series; confirm the value flows through."""
    from fabric_audit_agent.investigation.throttle import decompose_throttle
    rows = [{
        "capacityId": "c", "windowStartTime": "t1",
        "baseCapacityUnits": 64, "capacityUnitMs": 2016000,
        "overageTotalCapacityUnitMs": 960_000,
        "interactiveDelayThresholdPercentage": 1.05,
        "interactiveRejectionThresholdPercentage": 1.0,
        "backgroundRejectionThresholdPercentage": 1.0,
    }]
    series = capacity_series(lambda kql: rows)
    result = decompose_throttle(series, [])
    assert "minutesToBurndown" in result
    assert abs(result["minutesToBurndown"] - 0.25) < 0.001
