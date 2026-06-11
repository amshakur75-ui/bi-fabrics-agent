from fabric_audit_agent.reasoner_stub import create_stub_reasoner


def test_stub_maps_flag_to_seven_field_finding_with_key():
    flags = [{
        "type": "capacity.throttle", "resource": "Acme / capacity F64", "when": "t",
        "evidence": {"peakCuPct": 95, "throttleMinutes": 40, "sku": "F64"}, "what": "X reached 95% CU.",
    }]
    findings = create_stub_reasoner()["reason"]({}, flags)
    assert len(findings) == 1
    f = findings[0]
    assert f["what"] == "X reached 95% CU."
    assert f["where"] == "Acme / capacity F64"
    assert f["why"]  # from kb rootCause
    assert isinstance(f["fix"], list) and f["fix"]
    assert f["score"]["level"] == "Critical"
    assert f["key"] == "capacity.throttle::Acme / capacity F64"
    assert "peak window" in f["impact"]


def test_stub_unknown_flag_uses_kb_default_and_info_severity():
    findings = create_stub_reasoner()["reason"]({}, [{"type": "weird.thing", "resource": "r", "when": "", "evidence": {}, "what": "w"}])
    assert findings[0]["why"].startswith("Pattern not yet")
    assert findings[0]["impact"] == "Impact not assessed."
    assert findings[0]["score"]["level"] == "Info"
