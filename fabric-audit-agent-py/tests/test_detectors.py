from fabric_audit_agent.detectors.capacity import detect_capacity
from fabric_audit_agent.detectors.concentration import detect_concentration
from fabric_audit_agent.detectors.model import detect_models
from fabric_audit_agent.detectors.report import detect_reports
from fabric_audit_agent.detectors.pipeline import detect_pipelines
from fabric_audit_agent.detectors.blast_radius import detect_blast_radius
from fabric_audit_agent.detectors.security import detect_security
from fabric_audit_agent.detectors.cost import detect_cost


# ---------------- capacity ----------------
def _cap(**over):
    base = {
        "tenant": "Contoso", "capacityId": "F64", "sku": "F64", "memoryGB": 64,
        "peakCuPct": 96, "peakAt": "2026-06-08T06:05:00.000Z", "throttleMinutes": 42,
        "refreshes": [
            {"workspace": "Finance", "dataset": "Sales", "scheduledAt": "06:00", "durationMin": 47, "sizeGB": 4.2},
            {"workspace": "Finance", "dataset": "Forecast", "scheduledAt": "06:00", "durationMin": 31, "sizeGB": 2.1},
            {"workspace": "Ops", "dataset": "Logistics", "scheduledAt": "06:00", "durationMin": 22, "sizeGB": 1.4},
            {"workspace": "HR", "dataset": "Headcount", "scheduledAt": "09:00", "durationMin": 6, "sizeGB": 0.3},
        ],
    }
    base.update(over)
    return {"capacity": base}


def test_capacity_throttle_contention_oversized():
    assert sorted(f["type"] for f in detect_capacity(_cap())) == ["capacity.contention", "capacity.oversized-model", "capacity.throttle"]


def test_capacity_contention_lists_colliding_datasets():
    c = next(f for f in detect_capacity(_cap()) if f["type"] == "capacity.contention")
    assert c["evidence"]["datasets"] == ["Sales", "Forecast", "Logistics"]


def test_capacity_no_contention_from_blank_times():
    facts = _cap(refreshes=[{"workspace": "W", "dataset": f"D{i}", "scheduledAt": "", "durationMin": 1, "sizeGB": 0} for i in range(10)])
    assert [f for f in detect_capacity(facts) if f["type"] == "capacity.contention"] == []


def test_capacity_healthy_and_missing():
    healthy = {"capacity": {"tenant": "C", "capacityId": "F64", "sku": "F64", "memoryGB": 64, "peakCuPct": 40, "peakAt": "", "throttleMinutes": 0, "refreshes": []}}
    assert detect_capacity(healthy) == []
    assert detect_capacity({}) == []


# ---------------- concentration ----------------
def test_concentration_flags_over_threshold_skips_under():
    facts = {"items": [
        {"workspace": "Finance", "name": "GL Model", "kind": "SemanticModel", "cuSeconds": 700000, "sharePct": 70, "users": 12},
        {"workspace": "Sales", "name": "Exec", "kind": "Report", "cuSeconds": 100000, "sharePct": 10, "users": 80},
    ]}
    flags = detect_concentration(facts)
    assert len(flags) == 1 and flags[0]["evidence"]["sharePct"] == 70
    assert "GL Model" in flags[0]["what"] and "70%" in flags[0]["what"]


def test_concentration_named_users_top3_plus_count():
    facts = {"items": [{"workspace": "Finance", "name": "GL Model", "sharePct": 40, "userCount": 5,
                        "topUsers": [{"user": "jdoe@contoso.com"}, {"user": "asmith@contoso.com"}]}]}
    what = detect_concentration(facts)[0]["what"]
    assert "jdoe@contoso.com" in what and "asmith@contoso.com" in what and "+ 3 more" in what


def test_concentration_background_names_owner():
    facts = {"items": [{"workspace": "Fin", "name": "GL", "sharePct": 60, "background": True,
                        "owner": "owner@contoso.com", "topUsers": [{"user": "svc@contoso.com"}], "userCount": 1}]}
    what = detect_concentration(facts)[0]["what"]
    assert "background" in what and "owner@contoso.com" in what


def test_concentration_pending_and_custom_threshold():
    assert "pending" in detect_concentration({"items": [{"workspace": "Ops", "name": "Inv", "sharePct": 33, "users": 5}]})[0]["what"]
    cfg = {"capacity": {"concentrationPct": 50, "concentrationCritPct": 80}}
    assert detect_concentration({"items": [{"name": "x", "workspace": "w", "sharePct": 40}]}, cfg) == []


def test_concentration_integer_share_no_decimal():
    what = detect_concentration({"items": [{"name": "X", "workspace": "W", "sharePct": 70.0, "users": 1}]})[0]["what"]
    assert "70%" in what and "70.0%" not in what


# ---------------- model ----------------
def test_models_all_three_then_clean():
    dirty = {"models": [{"workspace": "Fin", "name": "GL", "bidirectionalRels": 6, "autoDateTime": True, "refreshFailRatePct": 12}]}
    assert sorted(f["type"] for f in detect_models(dirty)) == ["model.auto-datetime", "model.bidirectional", "model.refresh-failing"]
    clean = {"models": [{"workspace": "Fin", "name": "GL", "bidirectionalRels": 1, "autoDateTime": False, "refreshFailRatePct": 0}]}
    assert detect_models(clean) == []


# ---------------- report ----------------
def test_reports_three_branches():
    facts = {"reports": [{"workspace": "S", "name": "Exec", "visuals": 34, "mode": "DirectQuery", "slowestVisualMs": 8200}]}
    assert sorted(f["type"] for f in detect_reports(facts)) == ["report.directquery", "report.slow-visual", "report.too-many-visuals"]


# ---------------- pipeline ----------------
def test_pipeline_failing_status_rate_gateway():
    facts = {"pipelines": [
        {"workspace": "W", "name": "P1", "lastStatus": "Failed", "failRatePct": 0, "gatewayHealthy": True},
        {"workspace": "W", "name": "P2", "lastStatus": "Succeeded", "failRatePct": 15, "gatewayHealthy": False},
    ]}
    assert sorted(f["type"] for f in detect_pipelines(facts)) == ["pipeline.failing", "pipeline.failing", "pipeline.gateway"]


# ---------------- blast radius ----------------
def test_blast_radius_root_and_downstream():
    facts = {"lineage": {
        "nodes": [
            {"id": "a", "name": "Src", "type": "Dataset", "status": "Failed", "workspace": "W", "failedAt": "t"},
            {"id": "b", "name": "Mid", "type": "Dataset", "status": "Failed", "workspace": "W"},
            {"id": "c", "name": "Rpt", "type": "Report", "status": "OK", "workspace": "W"},
        ],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}],
    }}
    flags = detect_blast_radius(facts)
    assert len(flags) == 1   # only 'a' is a root cause (b has a Failed upstream)
    assert flags[0]["evidence"]["root"] == "Src"
    assert flags[0]["evidence"]["affected"] == ["Mid", "Rpt"]
    assert flags[0]["evidence"]["affectedCount"] == 2


def test_blast_radius_cycle_safe_and_empty():
    cyc = {"lineage": {
        "nodes": [{"id": "a", "name": "A", "type": "Dataset", "status": "Failed", "workspace": "W"},
                  {"id": "b", "name": "B", "type": "Dataset", "status": "OK", "workspace": "W"}],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
    }}
    assert detect_blast_radius(cyc)[0]["evidence"]["affected"] == ["B"]   # cycle back to root excluded
    assert detect_blast_radius({}) == []


# ---------------- security ----------------
def test_security_admin_external_unusual():
    facts = {"access": {
        "adminGrants": [{"workspace": "Fin", "principal": "u@x.com", "role": "Admin", "sensitive": True, "grantedAt": "t"}],
        "externalShares": [{"workspace": "Fin", "item": "Rpt", "sharedWith": "ext@y.com", "at": "t"}],
        "accessEvents": [{"workspace": "Fin", "user": "u@x.com", "count": 100, "baselineCount": 10}],
    }}
    flags = detect_security(facts)
    assert sorted(f["type"] for f in flags) == ["security.admin-grant", "security.external-share", "security.unusual-access"]
    assert next(f for f in flags if f["type"] == "security.unusual-access")["evidence"]["ratio"] == 10


def test_security_infinite_ratio_no_baseline():
    facts = {"access": {"accessEvents": [{"workspace": "Fin", "user": "u@x.com", "count": 50, "baselineCount": 0}]}}
    assert detect_security(facts)[0]["evidence"]["ratio"] == 999   # Infinity -> 999 sentinel


def test_security_non_admin_role_not_flagged():
    facts = {"access": {"adminGrants": [{"workspace": "Fin", "principal": "u@x.com", "role": "Viewer", "sensitive": True}]}}
    assert detect_security(facts) == []


# ---------------- cost ----------------
def test_cost_unused_and_idle_including_zero_cu():
    facts = {"usage": {
        "reports": [{"workspace": "W", "name": "Old", "views30d": 0}],
        "capacities": [{"id": "F64", "sku": "F64", "avgCuPct": 0}],   # 0% must flag idle (nullish ?? 100, not falsy)
    }}
    assert sorted(f["type"] for f in detect_cost(facts)) == ["cost.idle-capacity", "cost.unused-report"]


def test_cost_missing_avg_defaults_to_100_not_idle():
    assert detect_cost({"usage": {"capacities": [{"id": "F64", "sku": "F64"}]}}) == []   # no avgCuPct -> 100 -> not idle
