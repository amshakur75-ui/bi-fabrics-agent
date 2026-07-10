from fabric_audit_agent.data_agent import build_data_agent_manifest
from fabric_audit_agent.mappers import to_facts
from fabric_audit_agent.mappers.capacity import map_capacity
from fabric_audit_agent.eval import score_case, score_suite
from fabric_audit_agent.pipeline import run_audit
from fabric_audit_agent.reasoner_stub import create_stub_reasoner


# ---- data-agent ----
def test_data_agent_manifest():
    m = build_data_agent_manifest([{"name": "run_audit", "description": "d", "input_schema": {"type": "object"}}])
    assert m["name"] == "fabric-audit-agent" and m["readOnly"] is True
    assert m["tools"] == [{"name": "run_audit", "description": "d", "input_schema": {"type": "object"}}]


# ---- mappers ----
def test_map_capacity():
    raw = {"capacity": {"tenantName": "Acme", "displayName": "PROD", "sku": "F64", "memoryGb": 64, "peakCuPercent": 95, "peakTimestamp": "t", "throttledMinutes": 20},
           "refreshes": [{"groupName": "Fin", "datasetName": "GL", "scheduleTime": "06:00", "startTime": "2026-06-01T06:00:00Z", "endTime": "2026-06-01T06:40:00Z", "sizeBytes": 5_100_000_000}]}
    f = map_capacity(raw)["capacity"]
    assert f["tenant"] == "Acme" and f["capacityId"] == "PROD" and f["memoryGB"] == 64 and f["peakCuPct"] == 95
    assert f["refreshes"][0] == {"workspace": "Fin", "dataset": "GL", "scheduledAt": "06:00", "durationMin": 40, "sizeGB": 5.1}


def test_map_capacity_displayname_fallback_to_id():
    assert map_capacity({"capacity": {"id": "cap-123"}})["capacity"]["capacityId"] == "cap-123"


def test_to_facts_full_shape():
    raw = {"capacity": {"displayName": "P"},
           "datasets": [{"groupName": "W", "name": "M", "sizeBytes": 6_000_000_000, "relationshipsBidi": 6, "autoTimeIntelligence": True, "refreshFailureRatePct": 12}],
           "reports": [{"groupName": "W", "name": "R", "visualCount": 34, "storageMode": "DirectQuery", "slowestVisualMs": 8200, "datasourceType": "AzureSQL"}]}
    facts = to_facts(raw)
    assert facts["capacity"]["capacityId"] == "P"
    assert facts["models"][0] == {"workspace": "W", "name": "M", "sizeGB": 6.0, "bidirectionalRels": 6, "autoDateTime": True, "refreshFailRatePct": 12, "observedAt": ""}
    assert facts["reports"][0]["mode"] == "DirectQuery" and facts["reports"][0]["source"] == "AzureSQL"
    assert all(k in facts for k in ("pipelines", "lineage", "access", "usage"))


# ---- eval ----
def test_eval_score_case():
    sc = score_case([{"key": "capacity.throttle::a"}, {"key": "model.bidirectional::b"}],
                    {"types": ["capacity.throttle", "model.bidirectional", "report.directquery"]})
    assert sc["matched"] == 2 and sc["missing"] == ["report.directquery"] and sc["pass"] is False
    assert sc["recall"] == 0.67 and sc["precision"] == 1


def test_eval_skips_empty_string_type():
    # a "::orphan" key -> empty type; JS .filter(Boolean) drops it, so it is neither matched nor extra.
    sc = score_case([{"key": "::orphan"}, {"key": "capacity.throttle::a"}], {"types": ["capacity.throttle"]})
    assert sc["extra"] == [] and sc["matched"] == 1 and sc["precision"] == 1


def test_eval_perfect_and_suite():
    perfect = score_case([{"key": "capacity.throttle::a"}], {"types": ["capacity.throttle"]})
    assert perfect["pass"] is True and perfect["recall"] == 1 and perfect["precision"] == 1
    suite = score_suite([{"name": "c1", "score": perfect},
                         {"name": "c2", "score": score_case([], {"types": ["x.y"]})}])
    assert suite["cases"] == 2 and suite["passed"] == 1 and suite["failed"] == 1


# ---- pipeline (orchestrator) ----
def _opt_facts():
    return {"capacity": {"tenant": "Acme", "capacityId": "P", "sku": "F64", "memoryGB": 64, "peakCuPct": 95, "peakAt": "t", "throttleMinutes": 20,
                         "refreshes": [{"workspace": "Fin", "dataset": "A", "scheduledAt": "06:00", "durationMin": 10, "sizeGB": 6},
                                       {"workspace": "Fin", "dataset": "B", "scheduledAt": "06:00", "durationMin": 10, "sizeGB": 1},
                                       {"workspace": "Fin", "dataset": "C", "scheduledAt": "06:00", "durationMin": 10, "sizeGB": 1}]}}


def test_run_audit_end_to_end():
    delivered = {}
    env = run_audit(
        {"collect": lambda: _opt_facts()}, create_stub_reasoner(), {"deliver": lambda e: delivered.update(e)},
        agent_id="agent-1", now="2026-06-11T00:00:00Z", tenant="Acme",
    )
    assert env["success"] is True and env["agent_id"] == "agent-1"
    assert env["data"]["tenant"] == "Acme"
    assert env["data"]["verdict"]["decision"] == "optimize"
    assert 0 <= env["data"]["healthScore"]["overall"] <= 100
    assert "narrative" in env["data"] and "runLog" in env["data"]
    assert delivered  # delivery port was called
    assert all("confidence" in f for f in env["data"]["findings"])


def test_run_audit_with_store_appends_history():
    appended = []
    store = {
        "history": lambda: [{"runAt": "2026-06-10T00:00:00Z", "metrics": {"peakCuPct": 80}, "findings": [{"key": "capacity.throttle::Acme / capacity P"}]}],
        "append": lambda run: appended.append(run),
    }
    env = run_audit({"collect": lambda: _opt_facts()}, create_stub_reasoner(), {"deliver": lambda e: None},
                    store=store, agent_id="a", now="2026-06-11T00:00:00Z")
    assert len(appended) == 1 and appended[0]["tenant"] == "Acme"
    assert env["data"]["roadmap"]
    # Phase 6: the appended run records verdict + SLA state for alert-on-change comparison.
    assert appended[0]["verdictDecision"] == env["data"]["verdict"]["decision"]
    assert appended[0]["slaBreachedCount"] == (env["data"].get("sla") or {}).get("breachedCount", 0)


# ---- pipeline egress chokepoint (Phase 5.2 Task 2): delivered payload gated, return stays full ----
def _secret_finding(key="capacity.throttle::TestCap"):
    return {
        "what": "leaked", "where": "w", "when": "", "why": "y", "impact": "i",
        "fix": ["do x"], "score": {"level": "Critical", "value": 90},
        "key": key, "clientSecret": "s3cr3t",
    }


def test_run_audit_delivered_payload_is_gated_but_return_stays_full():
    delivered = {}
    reasoner = {"reason": lambda facts, flags: [_secret_finding()]}
    env = run_audit({"collect": lambda: _opt_facts()}, reasoner, {"deliver": lambda e: delivered.update(e)},
                    agent_id="a", now="2026-06-11T00:00:00Z")
    # delivered to the sink: masked
    assert delivered["data"]["findings"][0]["clientSecret"] == "***"
    # returned envelope: full/unmasked (feeds _write_outputs + callers; store already persisted earlier)
    assert env["data"]["findings"][0]["clientSecret"] == "s3cr3t"


def test_run_audit_over_budget_findings_capped_at_delivery_disclosed_in_summary_return_full():
    findings = [{
        "what": "x", "where": "w", "when": "", "why": "y", "impact": "i",
        "fix": ["do x"], "score": {"level": "Info", "value": 10},
        "key": f"capacity.throttle::T{i}", "blob": "z" * 500,
    } for i in range(30)]
    delivered = {}
    reasoner = {"reason": lambda facts, flags: findings}
    env = run_audit({"collect": lambda: _opt_facts()}, reasoner, {"deliver": lambda e: delivered.update(e)},
                    agent_id="a", now="2026-06-11T00:00:00Z")
    assert len(delivered["data"]["findings"]) < 30
    assert "omitted" in delivered["summary"]
    assert len(env["data"]["findings"]) == 30   # returned envelope uncapped/full
