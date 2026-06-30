from fabric_audit_agent.adapters.reasoner_investigation import create_investigation_reasoner


def _bundle(level="medium"):
    return {
        "subject": "user x@co",
        "coverage": {"workspacesSeen": ["Sales"], "sourcesFailed": []},
        "confidence": {"level": level, "basis": "single source"},
        "evidence": [{"kind": "query", "summary": "x@co = 40% monitored CU", "data": {"sharePct": 40}}],
    }


def test_stub_abstains_when_insufficient():
    out = create_investigation_reasoner()["investigate"](_bundle(level="insufficient"))
    assert "insufficient" in out["explanation"].lower()
    assert out["confidence"] == "insufficient"
    assert out["hypotheses"] == []


def test_stub_grounds_in_evidence_and_states_assumptions():
    out = create_investigation_reasoner()["investigate"](_bundle())
    assert "40%" in out["explanation"]                 # cites the evidence figure
    assert any("monitored" in a.lower() or "proxy" in a.lower() for a in out["assumptions"])
    assert out["whatWouldConfirm"]                      # always offers a confirmation path


