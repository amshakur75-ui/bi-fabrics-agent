from fabric_audit_agent.investigation.evidence import build_coverage, assess_confidence, evidence_item


def test_build_coverage_lists_workspaces_and_failed_sources():
    facts = {
        "items": [{"workspace": "Sales", "name": "A"}, {"workspace": "Ops", "name": "B"}],
        "users": [{"user": "x@co"}],
        "sourcesFailed": ["LA unreachable"],
    }
    cov = build_coverage(facts)
    assert set(cov["workspacesSeen"]) == {"Sales", "Ops"}
    assert cov["sourcesFailed"] == ["LA unreachable"]
    assert cov["mode"] == "live"          # users/items present -> not the mock fallback


def test_assess_confidence_insufficient_when_not_found():
    c = assess_confidence({"users": []}, found=False, corroborating_sources=0)
    assert c["level"] == "insufficient"


def test_assess_confidence_high_when_corroborated():
    c = assess_confidence({"users": [{"user": "x"}]}, found=True, corroborating_sources=2)
    assert c["level"] == "high"


def test_evidence_item_shape():
    e = evidence_item("query", "top user by CpuTimeMs", {"user": "x", "cuSeconds": 10})
    assert e == {"kind": "query", "summary": "top user by CpuTimeMs", "data": {"user": "x", "cuSeconds": 10}}
