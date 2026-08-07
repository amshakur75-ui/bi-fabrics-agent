import math

from fabric_audit_agent.detectors.absolute_cost import detect_absolute_cost


def _event(**overrides):
    ev = {"ts": "2026-08-07T06:00:00Z", "user": "alice@corp.com", "item": "Sales Model",
          "operation": "QueryEnd", "durationMs": 1000, "cuSeconds": 1.0, "queryText": "EVALUATE X"}
    ev.update(overrides)
    return ev


def test_fires_above_duration_threshold():
    ev = _event(durationMs=611_000, cuSeconds=1.0)   # 611s, well under CU threshold
    flags = detect_absolute_cost({"events": [ev]})
    assert len(flags) == 1
    f = flags[0]
    assert f["type"] == "activity.slow-operation"
    assert f["resource"] == "alice@corp.com"
    assert f["evidence"]["durationSeconds"] == 611.0
    assert "611.0s" in f["what"]


def test_fires_above_cu_threshold():
    ev = _event(durationMs=1000, cuSeconds=150.0)   # 1s duration, well under duration threshold
    flags = detect_absolute_cost({"events": [ev]})
    assert len(flags) == 1
    assert flags[0]["evidence"]["cuSeconds"] == 150.0


def test_below_both_thresholds_does_not_fire():
    ev = _event(durationMs=5000, cuSeconds=2.0)
    assert detect_absolute_cost({"events": [ev]}) == []


def test_boundary_values_fire_inclusive():
    ev = _event(durationMs=300_000, cuSeconds=1.0)   # exactly 300s == default threshold
    assert len(detect_absolute_cost({"events": [ev]})) == 1
    ev2 = _event(durationMs=1000, cuSeconds=100.0)   # exactly 100 CU-s == default threshold
    assert len(detect_absolute_cost({"events": [ev2]})) == 1


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
    ev = _event(durationMs=10_000, cuSeconds=5.0)   # 10s / 5 CU-s
    config = {"activity": {"slowOperationSeconds": 5, "highCuSeconds": 1000}}
    flags = detect_absolute_cost({"events": [ev]}, config)
    assert len(flags) == 1   # fires on the lowered duration threshold
