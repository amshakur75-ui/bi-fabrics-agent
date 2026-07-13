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


def test_top_users_finding_has_real_content_not_placeholder():
    # The informational "no single user over threshold" finding (capacity.user-ranking) must carry
    # real user-facing why/impact/fix -- never the developer placeholder scaffolding.
    flag = {"type": "capacity.user-ranking", "resource": "top-users", "when": "",
            "evidence": {"userCount": 488}, "what": "No single user is over 30%..."}
    f = create_stub_reasoner()["reason"]({}, [flag])[0]
    for field in ("why", "impact"):
        assert "not yet in the knowledge base" not in f[field].lower()
        assert "not assessed" not in f[field].lower()
    assert not any("add a playbook entry" in step.lower() for step in f["fix"])
    assert "distributed" in f["why"].lower() or "spread across" in f["why"].lower()
    assert f["fix"] and "no action" in f["fix"][0].lower()
