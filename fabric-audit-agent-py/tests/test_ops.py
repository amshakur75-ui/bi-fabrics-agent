from datetime import datetime
from fabric_audit_agent.accountability import first_seen_map, annotate_accountability, summarize_accountability
from fabric_audit_agent.sla import assess_sla, summarize_sla
from fabric_audit_agent.routing import route_findings
from fabric_audit_agent.ticket import build_ticket
from fabric_audit_agent.triggers import should_run_scheduled, evaluate_threshold_triggers
from fabric_audit_agent.outcomes import assess_outcomes, summarize_outcomes


def _ms(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000


# ---- accountability ----
def test_first_seen_map_earliest():
    hist = [{"runAt": "d1", "findings": [{"key": "a"}]}, {"runAt": "d2", "findings": [{"key": "a"}, {"key": "b"}]}]
    assert first_seen_map(hist) == {"a": "d1", "b": "d2"}


def test_annotate_accountability_open_and_recurring():
    findings = [
        {"key": "a", "recurringRuns": 4, "lifecycle": {"state": "open"}},
        {"key": "b", "recurringRuns": 4, "lifecycle": {"state": "acknowledged"}},
        {"key": "c", "recurringRuns": 1},
    ]
    out = annotate_accountability(findings, [{"runAt": "d1", "findings": [{"key": "a"}]}])
    by = {f["key"]: f for f in out}
    assert by["a"]["accountability"]["openRuns"] == 4 and by["a"]["accountability"]["firstSeen"] == "d1"
    assert "accountability" not in by["b"] and "accountability" not in by["c"]
    assert summarize_accountability(out)["ignoredCount"] == 1


# ---- sla ----
def test_assess_sla_breach_and_summary():
    findings = [{"key": "a", "score": {"level": "Critical"}}]
    hist = [{"runAt": "2026-06-01T00:00:00Z", "findings": [{"key": "a"}]}]
    out = assess_sla(findings, hist, _ms("2026-06-05T00:00:00Z"))   # 4 days; Critical target 1
    assert out[0]["sla"]["ageDays"] == 4 and out[0]["sla"]["breached"] is True
    assert summarize_sla(out)["breachedCount"] == 1


def test_assess_sla_skips_without_now():
    out = assess_sla([{"key": "a", "score": {"level": "Critical"}}], [{"runAt": "2026-06-01T00:00:00Z", "findings": [{"key": "a"}]}], 0)
    assert "sla" not in out[0]


# ---- routing ----
def test_route_findings_by_domain():
    r = route_findings([{"key": "security.admin-grant::a"}, {"key": "capacity.throttle::b"}, {"key": "weird::c"}])
    assert r["security-team"] == ["security.admin-grant::a"]
    assert r["powerbi-team"] == ["capacity.throttle::b"]
    assert r["unrouted"] == ["weird::c"]


# ---- ticket ----
def test_build_ticket():
    t = build_ticket({"key": "capacity.throttle::x", "score": {"level": "Critical"}, "what": "Throttling", "where": "cap F64", "why": "demand", "impact": "slow", "fix": ["a", "b"]})
    assert t["title"] == "[Critical] Throttling" and t["severity"] == "Critical"
    assert t["labels"] == ["fabric-audit", "capacity"]
    assert "- a\n- b" in t["body"] and t["externalKey"] == "capacity.throttle::x"


# ---- triggers ----
def test_should_run_scheduled():
    assert should_run_scheduled({"cadence": "daily", "atHour": 6, "atMinute": 0}, {"hour": 6, "minute": 0, "dayOfWeek": 3}) is True
    assert should_run_scheduled({"cadence": "daily", "atHour": 6, "atMinute": 0}, {"hour": 7, "minute": 0, "dayOfWeek": 3}) is False
    assert should_run_scheduled({"cadence": "hourly", "atMinute": 30}, {"hour": 9, "minute": 30, "dayOfWeek": 3}) is True
    assert should_run_scheduled({"cadence": "weekly", "atHour": 6, "atMinute": 0, "dayOfWeek": 1}, {"hour": 6, "minute": 0, "dayOfWeek": 1}) is True
    assert should_run_scheduled({"cadence": "weekly", "atHour": 6, "atMinute": 0, "dayOfWeek": 1}, {"hour": 6, "minute": 0, "dayOfWeek": 2}) is False


def test_evaluate_threshold_triggers():
    facts = {"capacity": {"capacityId": "F64", "peakCuPct": 95},
             "pipelines": [{"name": "P", "lastStatus": "Failed"}],
             "access": {"adminGrants": [{"role": "Admin", "sensitive": True, "workspace": "W"}]}}
    events = evaluate_threshold_triggers(facts)
    reasons = " ".join(e["reason"] for e in events)
    assert "95% CU" in reasons and 'Pipeline "P" failed' in reasons and "Admin grant" in reasons
    assert all(e["severity"] == "Critical" for e in events)


# ---- outcomes ----
def test_assess_outcomes_resolved_and_metric():
    prev = {"metrics": {"peakCuPct": 90}, "findings": [{"key": "a"}, {"key": "b"}, {"key": "c", "suppressed": True}]}
    out = assess_outcomes([{"key": "a"}], [prev], current_metric=80)
    assert out["resolvedSinceLast"] == ["b"]   # b active prev, gone now; c was suppressed; a still present
    assert out["metricDelta"]["change"] == -10 and out["metricDelta"]["improved"] is True
    s = summarize_outcomes(out)
    assert "resolved since the last run" in s and "improved 90% → 80%" in s


def test_assess_outcomes_no_history():
    assert assess_outcomes([{"key": "a"}], []) == {"resolvedSinceLast": [], "metricDelta": None}


# ---- review-driven coverage ----
def test_accountability_missing_lifecycle_treated_open():
    out = annotate_accountability([{"key": "a", "recurringRuns": 3}], [{"runAt": "d1", "findings": [{"key": "a"}]}])
    assert "accountability" in out[0]   # no lifecycle -> default open -> annotated


def test_accountability_empty_state_not_open_and_pure():
    src = [{"key": "a", "recurringRuns": 3, "lifecycle": {"state": ""}}]
    out = annotate_accountability(src, [])
    assert "accountability" not in out[0]   # "" is not open (nullish parity)
    assert "accountability" not in src[0]   # input untouched


def test_sla_malformed_runat_unmodified():
    out = assess_sla([{"key": "a", "score": {"level": "Critical"}}], [{"runAt": "not-a-date", "findings": [{"key": "a"}]}], _ms("2026-06-05T00:00:00Z"))
    assert "sla" not in out[0]


def test_route_custom_routes_override():
    assert route_findings([{"key": "cost.idle::a"}], {"cost": "finops"})["finops"] == ["cost.idle::a"]


def test_ticket_empty_level_and_what_preserved():
    t = build_ticket({"score": {"level": ""}, "what": ""})
    assert t["title"] == "[] " and t["severity"] == ""   # nullish: empty strings preserved (matches Node)


def test_triggers_boundary_and_sensitive_false():
    assert evaluate_threshold_triggers({"capacity": {"capacityId": "F", "peakCuPct": 90}}) != []   # 90 >= 90 crit
    assert evaluate_threshold_triggers({"capacity": {"capacityId": "F", "peakCuPct": 89}}) == []
    assert evaluate_threshold_triggers({"access": {"adminGrants": [{"role": "Admin", "sensitive": False, "workspace": "W"}]}}) == []


def test_outcomes_not_improved_and_same_value():
    up = assess_outcomes([], [{"metrics": {"peakCuPct": 80}, "findings": []}], current_metric=90)
    assert up["metricDelta"]["change"] == 10 and up["metricDelta"]["improved"] is False
    same = assess_outcomes([], [{"metrics": {"peakCuPct": 80}, "findings": []}], current_metric=80)
    assert same["metricDelta"]["change"] == 0 and same["metricDelta"]["improved"] is False
