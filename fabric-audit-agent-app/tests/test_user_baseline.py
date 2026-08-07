from fabric_audit_agent.detectors.user_baseline import detect_user_baseline_deviation


def _history_rows(cu_values):
    return [{"cuSeconds": v, "operation": "QueryEnd", "hourUtc": 6} for v in cu_values]


def _event(**overrides):
    ev = {"ts": "2026-08-07T06:00:00Z", "user": "alice@corp.com", "item": "Sales Model",
          "operation": "QueryEnd", "cuSeconds": 5.0}
    ev.update(overrides)
    return ev


def test_fires_when_event_exceeds_own_p95():
    history = {"alice@corp.com": _history_rows([1, 1, 1, 1, 1, 1, 1, 1, 1, 20])}   # p95 well below 100
    ev = _event(user="alice@corp.com", cuSeconds=100.0)
    flags = detect_user_baseline_deviation([ev], history)
    assert len(flags) == 1
    f = flags[0]
    assert f["type"] == "activity.user-baseline-deviation"
    assert f["resource"] == "alice@corp.com"
    assert f["evidence"]["cuSeconds"] == 100.0
    assert f["evidence"]["baselineCount"] == 10


def test_no_flag_when_within_baseline():
    history = {"alice@corp.com": _history_rows([5, 5, 5, 5, 5, 5])}
    ev = _event(user="alice@corp.com", cuSeconds=5.0)
    assert detect_user_baseline_deviation([ev], history) == []


def test_no_flag_when_user_has_no_history():
    ev = _event(user="alice@corp.com", cuSeconds=100.0)
    assert detect_user_baseline_deviation([ev], {}) == []
    assert detect_user_baseline_deviation([ev], None) == []


def test_no_flag_when_history_below_min_count():
    history = {"alice@corp.com": _history_rows([1, 1])}   # below default baselineMinHistory=5
    ev = _event(user="alice@corp.com", cuSeconds=100.0)
    assert detect_user_baseline_deviation([ev], history) == []


def test_different_user_history_not_cross_applied():
    history = {"bob@corp.com": _history_rows([1, 1, 1, 1, 1, 1, 1, 1, 1, 1])}
    ev = _event(user="alice@corp.com", cuSeconds=100.0)
    assert detect_user_baseline_deviation([ev], history) == []


def test_empty_events_no_flags():
    history = {"alice@corp.com": _history_rows([1, 1, 1, 1, 1, 1])}
    assert detect_user_baseline_deviation([], history) == []
    assert detect_user_baseline_deviation(None, history) == []


def test_custom_min_history_from_config():
    history = {"alice@corp.com": _history_rows([1, 1, 1])}   # 3 rows
    ev = _event(user="alice@corp.com", cuSeconds=100.0)
    config = {"activity": {"baselineMinHistory": 3}}
    flags = detect_user_baseline_deviation([ev], history, config)
    assert len(flags) == 1


def test_flag_carries_no_capacity_percentage_key():
    history = {"alice@corp.com": _history_rows([1, 1, 1, 1, 1, 1, 1, 1, 1, 1])}
    ev = _event(user="alice@corp.com", cuSeconds=100.0)
    f = detect_user_baseline_deviation([ev], history)[0]
    evidence_keys = set(f["evidence"].keys())
    assert not any("capacity" in k.lower() or "share" in k.lower() for k in evidence_keys)
    assert "capacity" not in f["what"].lower()
