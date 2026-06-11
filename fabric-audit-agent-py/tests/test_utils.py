from fabric_audit_agent.sanitize import sanitize, sanitize_evidence
from fabric_audit_agent.validate import validate_facts
from fabric_audit_agent.confidence import score_confidence
from fabric_audit_agent.run_log import build_run_log


# ---- sanitize ----
def test_sanitize_evidence_keeps_safe_drops_identifying():
    out = sanitize_evidence({"peakCuPct": 95, "sku": "F64", "dataset": "Secret GL", "datasets": ["a", "b", "c"], "ok": True, "time": "06:00", "source": "secret"})
    assert out["peakCuPct"] == 95
    assert out["sku"] == "F64"            # safe enum kept
    assert out["ok"] is True              # bool kept
    assert out["time"] == "06:00"         # safe enum kept
    assert out["datasetsCount"] == 3      # array -> count
    assert "dataset" not in out and "source" not in out   # identifying strings dropped


def test_sanitize_evidence_redacts_sensitive():
    assert sanitize_evidence({"sensitive": True, "x": 1}) == {"redacted": True}
    assert sanitize_evidence({"sensitivityLabel": "Confidential", "x": 1}) == {"redacted": True}


def test_sanitize_flags_indexed_payload_no_names():
    s = sanitize([{"type": "capacity.throttle", "resource": "secret/ws", "what": "secret", "evidence": {"peakCuPct": 95, "datasets": ["x"]}}])
    assert s == [{"id": 0, "type": "capacity.throttle", "evidence": {"peakCuPct": 95, "datasetsCount": 1}}]


# ---- validate ----
def test_validate_ok_when_domains_absent():
    assert validate_facts({}) == {"ok": True, "issues": []}


def test_validate_flags_missing_fields_and_bad_arrays():
    r = validate_facts({"capacity": {"capacityId": "F64"}, "models": {}, "lineage": {"nodes": "x"}})
    issues = {(i["domain"], i["issue"]) for i in r["issues"]}
    assert ("capacity", "missing sku") in issues
    assert ("capacity", "missing memoryGB") in issues
    assert ("models", "expected an array") in issues
    assert ("lineage", "nodes must be an array") in issues
    assert r["ok"] is False


def test_validate_refreshes_must_be_array():
    r = validate_facts({"capacity": {"capacityId": "c", "sku": "F64", "memoryGB": 64, "peakCuPct": 50, "refreshes": "nope"}})
    assert ("capacity", "refreshes must be an array") in {(i["domain"], i["issue"]) for i in r["issues"]}


# ---- confidence ----
def test_confidence_levels():
    assert score_confidence({"key": "meta.detector-error::x"}) == "low"
    assert score_confidence({"key": "capacity.throttle::x", "reasonedBy": "claude"}) == "medium"
    assert score_confidence({"key": "capacity.throttle::x"}) == "high"
    assert score_confidence({}) == "high"


# ---- run-log ----
def test_run_log_summarizes_run():
    log = build_run_log({"capacity": {}, "models": [], "usage": {"reports": []}},
                        {"data": {"findings": [1, 2], "suppressed": [3]}}, "2026-06-11T00:00:00Z")
    assert log["at"] == "2026-06-11T00:00:00Z"
    assert "capacity" in log["collectedDomains"] and "usage" in log["collectedDomains"]
    assert "lineage" not in log["collectedDomains"]
    assert log["findingCount"] == 2 and log["suppressedCount"] == 1
    assert log["readOnly"] is True
