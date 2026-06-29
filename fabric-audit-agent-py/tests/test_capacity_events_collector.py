"""Tests for the capacity-events collector (CU% / throttle from Real-Time Hub Capacity Overview Events)."""
from fabric_audit_agent.adapters.collector_capacity_events import create_capacity_events_collector

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
