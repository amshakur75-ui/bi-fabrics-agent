from fabric_audit_agent.severity import score_severity


def test_throttle_warning_then_critical():
    assert score_severity({"type": "capacity.throttle", "evidence": {"peakCuPct": 85, "throttleMinutes": 5}})["level"] == "Warning"
    assert score_severity({"type": "capacity.throttle", "evidence": {"peakCuPct": 95, "throttleMinutes": 40}})["level"] == "Critical"


def test_concentration_threshold():
    assert score_severity({"type": "capacity.concentration", "evidence": {"sharePct": 65}})["level"] == "Critical"
    assert score_severity({"type": "capacity.concentration", "evidence": {"sharePct": 35}})["level"] == "Warning"


def test_contention_counts_datasets():
    s = score_severity({"type": "capacity.contention", "evidence": {"datasets": ["a", "b", "c", "d"], "time": "06:00"}})
    assert s["level"] == "Critical"
    assert "4 models refresh at 06:00" in s["reason"]


def test_oversized_uses_memory_fraction():
    crit = score_severity({"type": "capacity.oversized-model", "evidence": {"sizeGB": 20, "memoryGB": 64}})
    warn = score_severity({"type": "capacity.oversized-model", "evidence": {"sizeGB": 5, "memoryGB": 64}})
    assert crit["level"] == "Critical"   # 20 >= 25% of 64 (16)
    assert warn["level"] == "Warning"    # 5 < 16


def test_unknown_type_is_info():
    assert score_severity({"type": "nope", "evidence": {}})["level"] == "Info"
