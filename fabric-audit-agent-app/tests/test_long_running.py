import math

from fabric_audit_agent.detectors.long_running import detect_long_running_cluster


def _event(**overrides):
    ev = {"ts": "2026-08-07T06:00:00Z", "user": "alice@corp.com", "item": "Sales Model",
          "operation": "QueryEnd", "durationMs": 310_000, "cuSeconds": 1.0, "queryText": "EVALUATE X"}
    ev.update(overrides)
    return ev


def test_fires_on_cluster_of_three_long_ops_same_item():
    events = [
        _event(user="alice@corp.com", durationMs=310_000),
        _event(user="bob@corp.com", durationMs=320_000),
        _event(user="carol@corp.com", durationMs=330_000),
    ]
    flags = detect_long_running_cluster({"events": events})
    assert len(flags) == 1
    f = flags[0]
    assert f["type"] == "activity.long-running-cluster"
    assert f["resource"] == "Sales Model"
    assert f["evidence"]["item"] == "Sales Model"
    assert f["evidence"]["count"] == 3
    assert f["evidence"]["maxDurationMs"] == 330_000
    assert f["evidence"]["users"] == ["alice@corp.com", "bob@corp.com", "carol@corp.com"]


def test_below_cluster_min_does_not_fire():
    events = [
        _event(user="alice@corp.com", durationMs=310_000),
        _event(user="bob@corp.com", durationMs=320_000),
    ]
    assert detect_long_running_cluster({"events": events}) == []


def test_different_items_do_not_cluster_together():
    events = [
        _event(item="Model A", durationMs=310_000),
        _event(item="Model B", durationMs=320_000),
        _event(item="Model C", durationMs=330_000),
    ]
    assert detect_long_running_cluster({"events": events}) == []


def test_short_ops_do_not_count_toward_cluster():
    events = [
        _event(durationMs=310_000),
        _event(durationMs=320_000),
        _event(durationMs=5_000),   # short, doesn't count
    ]
    assert detect_long_running_cluster({"events": events}) == []


def test_single_op_alone_below_threshold_never_flagged():
    events = [_event(durationMs=310_000)]
    assert detect_long_running_cluster({"events": events}) == []


def test_boundary_value_counts_inclusive():
    events = [
        _event(durationMs=300_000),
        _event(durationMs=300_000),
        _event(durationMs=300_000),
    ]
    assert len(detect_long_running_cluster({"events": events})) == 1


def test_bool_and_nan_duration_rejected():
    events = [
        _event(durationMs=True),
        _event(durationMs=float("nan")),
        _event(durationMs=math.inf),
    ]
    # inf is finite-rejected by _num, bool rejected, nan rejected -> none count
    assert detect_long_running_cluster({"events": events}) == []


def test_empty_events_no_flags():
    assert detect_long_running_cluster({}) == []
    assert detect_long_running_cluster({"events": []}) == []
    assert detect_long_running_cluster(None) == []


def test_flag_carries_no_capacity_percentage_key():
    events = [_event(durationMs=310_000 + i * 1000) for i in range(3)]
    f = detect_long_running_cluster({"events": events})[0]
    evidence_keys = set(f["evidence"].keys())
    assert not any("pct" in k.lower() or "share" in k.lower() or "capacity" in k.lower()
                   for k in evidence_keys)
    assert "capacity" not in f["what"].lower()
    assert "%" not in f["what"]


def test_users_list_capped_at_five():
    events = [_event(user=f"user{i}@corp.com", durationMs=310_000) for i in range(7)]
    f = detect_long_running_cluster({"events": events})[0]
    assert f["evidence"]["count"] == 7
    assert len(f["evidence"]["users"]) == 5


def test_custom_thresholds_from_config():
    events = [_event(durationMs=10_000) for _ in range(2)]
    config = {"activity": {"longRunningSeconds": 5, "longRunningClusterMin": 2}}
    flags = detect_long_running_cluster({"events": events}, config)
    assert len(flags) == 1
