from fabric_audit_agent.detectors import detect_all


def test_detect_all_aggregates_across_domains():
    facts = {
        "capacity": {"tenant": "C", "capacityId": "F64", "sku": "F64", "memoryGB": 64,
                     "peakCuPct": 96, "peakAt": "t", "throttleMinutes": 42, "refreshes": []},
        "models": [{"workspace": "Fin", "name": "GL", "bidirectionalRels": 9, "autoDateTime": False, "refreshFailRatePct": 0}],
        "items": [{"workspace": "Fin", "name": "GL", "sharePct": 80, "users": 3}],
    }
    types = {f["type"] for f in detect_all(facts)}
    assert {"capacity.throttle", "model.bidirectional", "capacity.concentration"} <= types


def test_detect_all_isolates_a_failing_detector():
    def boom(facts, config):
        raise RuntimeError("kaboom")
    flags = detect_all({}, detectors=[boom])
    assert len(flags) == 1
    assert flags[0]["type"] == "meta.detector-error"
    assert "kaboom" in flags[0]["evidence"]["message"]
    assert flags[0]["evidence"]["detector"] == "boom"


def test_detect_all_empty_facts_no_crash():
    assert detect_all({}) == []


def test_detect_all_mixed_real_and_throwing_detector():
    from fabric_audit_agent.detectors.capacity import detect_capacity

    def boom(facts, config):
        raise RuntimeError("kaboom")

    facts = {"capacity": {"tenant": "C", "capacityId": "F64", "sku": "F64", "memoryGB": 64,
                          "peakCuPct": 96, "peakAt": "t", "throttleMinutes": 42, "refreshes": []}}
    flags = detect_all(facts, detectors=[detect_capacity, boom])
    types = sorted(f["type"] for f in flags)
    assert "capacity.throttle" in types and "meta.detector-error" in types
