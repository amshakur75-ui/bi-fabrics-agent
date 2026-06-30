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
    c = assess_confidence(found=False, corroborating_sources=0)
    assert c["level"] == "insufficient"


def test_assess_confidence_high_when_corroborated():
    c = assess_confidence(found=True, corroborating_sources=2)
    assert c["level"] == "high"


def test_evidence_item_shape():
    e = evidence_item("query", "top user by CpuTimeMs", {"user": "x", "cuSeconds": 10})
    assert e == {"kind": "query", "summary": "top user by CpuTimeMs", "data": {"user": "x", "cuSeconds": 10}}


# --- Group 1: coverage honesty ---

def test_build_coverage_items_only_uses_inventory_not_attribution():
    """Items present but NO users -> inventory signal, not user attribution."""
    facts = {"items": [{"workspace": "Sales", "name": "A"}]}
    cov = build_coverage(facts)
    assert "inventory" in cov["sources"]
    assert "attribution" not in cov["sources"]


def test_build_coverage_users_present_uses_attribution():
    """When users are present -> attribution label."""
    facts = {
        "items": [{"workspace": "Sales", "name": "A"}],
        "users": [{"user": "x@co"}],
    }
    cov = build_coverage(facts)
    assert "attribution" in cov["sources"]


def test_build_coverage_mock_mode_has_blind_list():
    """Empty facts (mock mode) -> blind list is non-empty."""
    cov = build_coverage({})
    assert cov["mode"] == "mock"
    assert isinstance(cov["blind"], list)
    assert len(cov["blind"]) > 0


def test_build_coverage_live_mode_blind_is_empty():
    """Live facts -> blind list is empty."""
    facts = {"users": [{"user": "x@co"}], "capacity": {"peakCuPct": 80}}
    cov = build_coverage(facts)
    assert cov["mode"] == "live"
    assert cov["blind"] == []
