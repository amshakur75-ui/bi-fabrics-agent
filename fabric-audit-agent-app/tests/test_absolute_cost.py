import math

from fabric_audit_agent.detectors.absolute_cost import detect_absolute_cost


def _event(**overrides):
    ev = {"ts": "2026-08-07T06:00:00Z", "user": "alice@corp.com", "item": "Sales Model",
          "operation": "QueryEnd", "durationMs": 1000, "cuSeconds": 1.0, "queryText": "EVALUATE X"}
    ev.update(overrides)
    return ev


def test_fires_only_when_BOTH_slow_and_costly():
    # 2026-08-09 fix: the gate is now `slow AND costly` (was OR). A query must cross BOTH bars.
    # Long-but-light events (611s @ 1 CPU-s) no longer fire — they're I/O-bound, not capacity-heavy.
    # Short-but-costly events (1s @ 150 CPU-s) no longer fire — normal spike for a busy user.
    ev = _event(durationMs=611_000, cuSeconds=1.0)
    assert detect_absolute_cost({"events": [ev]}) == []
    ev = _event(durationMs=1000, cuSeconds=150.0)
    assert detect_absolute_cost({"events": [ev]}) == []
    # A query that IS both slow AND heavy does fire — Bipin-shape (long + expensive).
    ev = _event(durationMs=611_000, cuSeconds=150.0)
    flags = detect_absolute_cost({"events": [ev]})
    assert len(flags) == 1
    f = flags[0]
    assert f["type"] == "activity.slow-operation"
    assert f["resource"] == "alice@corp.com"
    assert f["evidence"]["durationSeconds"] == 611.0
    # honest card labels: CPU-s + Wall-clock + Intensity (added 2026-08-09)
    assert "611.0s" in f["what"] and "CPU-s" in f["what"]
    assert "CU-s" not in f["what"]   # the misleading legacy label is retired
    assert f["evidence"]["cpuSeconds"] == 150.0
    assert f["evidence"]["wallSeconds"] == 611.0
    assert f["evidence"]["intensityCpuPerSec"] is not None


def test_below_both_thresholds_does_not_fire():
    ev = _event(durationMs=5000, cuSeconds=2.0)
    assert detect_absolute_cost({"events": [ev]}) == []


def test_boundary_values_fire_inclusive():
    # Both bars exactly at the default thresholds: 300s AND 100 CPU-s -> fires (inclusive AND).
    ev = _event(durationMs=300_000, cuSeconds=100.0)
    assert len(detect_absolute_cost({"events": [ev]})) == 1
    # Just below EITHER bar -> silent (AND requires both).
    ev = _event(durationMs=299_000, cuSeconds=100.0)
    assert detect_absolute_cost({"events": [ev]}) == []
    ev = _event(durationMs=300_000, cuSeconds=99.0)
    assert detect_absolute_cost({"events": [ev]}) == []


def test_bool_cost_rejected():
    ev = _event(durationMs=True, cuSeconds=True)   # bool must never satisfy a numeric threshold
    assert detect_absolute_cost({"events": [ev]}) == []


def test_nan_cost_rejected():
    ev = _event(durationMs=float("nan"), cuSeconds=float("nan"))
    assert detect_absolute_cost({"events": [ev]}) == []


def test_inf_cost_rejected():
    ev = _event(durationMs=math.inf, cuSeconds=math.inf)
    assert detect_absolute_cost({"events": [ev]}) == []


def test_empty_events_no_flags():
    assert detect_absolute_cost({}) == []
    assert detect_absolute_cost({"events": []}) == []
    assert detect_absolute_cost(None) == []


def test_flag_carries_no_capacity_percentage_key():
    ev = _event(durationMs=611_000, cuSeconds=150.0)
    f = detect_absolute_cost({"events": [ev]})[0]
    evidence_keys = set(f["evidence"].keys())
    assert not any("pct" in k.lower() or "share" in k.lower() or "capacity" in k.lower()
                   for k in evidence_keys)
    assert "capacity" not in f["what"].lower()
    assert "%" not in f["what"]


def test_custom_thresholds_from_config():
    # Under AND: crossing only one custom threshold isn't enough — both must cross.
    ev = _event(durationMs=10_000, cuSeconds=5.0)   # 10s / 5 CPU-s
    lowered_dur_only = {"activity": {"slowOperationSeconds": 5, "highCuSeconds": 1000}}
    assert detect_absolute_cost({"events": [ev]}, lowered_dur_only) == []   # CU still below
    both_lowered = {"activity": {"slowOperationSeconds": 5, "highCuSeconds": 4}}
    assert len(detect_absolute_cost({"events": [ev]}, both_lowered)) == 1   # both cross
