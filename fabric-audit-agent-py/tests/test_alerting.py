"""Tests for the Phase 6 alert-on-change decision (automation/alerting.py). Pure/offline."""
from fabric_audit_agent.automation.alerting import decide_alert


def _env(findings=None, suppressed=None, verdict=None, sla=None):
    data = {"findings": findings or []}
    if suppressed is not None:
        data["suppressed"] = suppressed
    if verdict is not None:
        data["verdict"] = verdict
    if sla is not None:
        data["sla"] = sla
    return {"summary": "s", "data": data}


def _cf(key, level="Warning"):
    """Current-envelope finding (score.level)."""
    return {"key": key, "score": {"level": level, "reason": "r"}}


def _hf(key, level="Warning", suppressed=False):
    """History finding (flat level)."""
    return {"key": key, "level": level, "where": "w", "what": "x", "suppressed": suppressed}


def _run(findings=None, verdict_decision=None, sla_breached=None):
    r = {"runAt": "t", "findings": findings or []}
    if verdict_decision is not None:
        r["verdictDecision"] = verdict_decision
    if sla_breached is not None:
        r["slaBreachedCount"] = sla_breached
    return r


# ---- individual signals fire ----
def test_new_finding_at_warning_alerts():
    env = _env([_cf("a", "Warning")])
    prev = [_run([_hf("z", "Warning")])]
    out = decide_alert(env, prev)
    assert out["alert"] is True
    assert out["changes"]["new"] == ["a"]


def test_worsened_finding_alerts():
    env = _env([_cf("a", "Critical")])
    prev = [_run([_hf("a", "Warning")])]
    out = decide_alert(env, prev)
    assert out["alert"] is True
    assert out["changes"]["worsened"] == ["a"]
    assert "new" not in out["changes"]


def test_resolved_finding_alerts():
    env = _env([])   # 'a' gone
    prev = [_run([_hf("a", "Warning")])]
    out = decide_alert(env, prev)
    assert out["alert"] is True
    assert out["changes"]["resolved"] == ["a"]


def test_verdict_worse_alerts():
    env = _env([], verdict={"decision": "size-up"})
    prev = [_run([], verdict_decision="optimize")]
    out = decide_alert(env, prev)
    assert out["alert"] is True
    assert out["changes"]["verdictChange"] == {"from": "optimize", "to": "size-up"}


def test_sla_increase_alerts():
    env = _env([], sla={"breachedCount": 2})
    prev = [_run([], sla_breached=1)]
    out = decide_alert(env, prev)
    assert out["alert"] is True
    assert out["changes"]["slaIncrease"] == {"from": 1, "to": 2}


# ---- no-alert / low-noise cases ----
def test_no_change_no_alert():
    env = _env([_cf("a", "Warning")], verdict={"decision": "optimize"}, sla=None)
    prev = [_run([_hf("a", "Warning")], verdict_decision="optimize", sla_breached=0)]
    out = decide_alert(env, prev)
    assert out["alert"] is False
    assert out["changes"] == {}
    assert out["reason"] == "no material change"


def test_standing_sla_breach_does_not_realert():
    env = _env([], sla={"breachedCount": 3})
    prev = [_run([], sla_breached=3)]
    out = decide_alert(env, prev)
    assert out["alert"] is False
    assert "slaIncrease" not in out["changes"]


def test_verdict_improvement_is_recorded_not_material():
    env = _env([], verdict={"decision": "healthy"})
    prev = [_run([], verdict_decision="size-up")]
    out = decide_alert(env, prev)
    assert out["alert"] is False
    assert out["changes"]["verdictChange"] == {"from": "size-up", "to": "healthy"}


def test_unknown_verdict_transition_recorded_not_material():
    env = _env([], verdict={"decision": "unknown"})
    prev = [_run([], verdict_decision="healthy")]
    out = decide_alert(env, prev)
    assert out["alert"] is False
    assert out["changes"]["verdictChange"] == {"from": "healthy", "to": "unknown"}


# ---- snooze-safe ----
def test_snoozed_finding_not_reported_resolved():
    # 'a' was active last run; this run it's suppressed (snoozed), not in data.findings.
    env = _env([], suppressed=[{"key": "a", "state": "snoozed", "what": "x"}])
    prev = [_run([_hf("a", "Warning")])]
    out = decide_alert(env, prev)
    assert out["alert"] is False
    assert "resolved" not in out["changes"]


# ---- min_level gating ----
def test_info_only_new_finding_below_default_floor():
    env = _env([_cf("a", "Info")])
    prev = [_run([])]
    assert decide_alert(env, prev)["alert"] is False


def test_info_finding_alerts_when_floor_lowered():
    env = _env([_cf("a", "Info")])
    prev = [_run([])]
    out = decide_alert(env, prev, min_level="Info")
    assert out["alert"] is True
    assert out["changes"]["new"] == ["a"]


def test_changes_lists_only_qualifying_keys():
    env = _env([_cf("a", "Warning"), _cf("b", "Info")])
    prev = [_run([])]
    out = decide_alert(env, prev)   # default Warning floor
    assert out["changes"]["new"] == ["a"]   # b (Info) excluded


# ---- first run / baseline ----
def test_first_run_baseline_alerts_on_new_material():
    env = _env([_cf("a", "Critical")])
    out = decide_alert(env, [])
    assert out["alert"] is True
    assert out["reason"] == "baseline"
    assert out["changes"]["new"] == ["a"]


def test_first_run_no_resolved_or_verdict():
    env = _env([_cf("a", "Warning")], verdict={"decision": "size-up"})
    out = decide_alert(env, [])
    assert "resolved" not in out["changes"]
    assert "verdictChange" not in out["changes"]


def test_first_run_first_sla_breach_counts():
    env = _env([], sla={"breachedCount": 1})
    out = decide_alert(env, [])
    assert out["alert"] is True
    assert out["changes"]["slaIncrease"] == {"from": 0, "to": 1}


# ---- graceful degrade on missing history fields ----
def test_missing_verdict_decision_field_no_crash_no_false_positive():
    env = _env([], verdict={"decision": "size-up"})
    prev = [_run([])]   # no verdictDecision key
    out = decide_alert(env, prev)
    assert out["alert"] is False
    assert "verdictChange" not in out["changes"]


def test_missing_sla_field_treated_as_zero():
    env = _env([], sla={"breachedCount": 2})
    prev = [_run([])]   # no slaBreachedCount key
    out = decide_alert(env, prev)
    assert out["alert"] is True
    assert out["changes"]["slaIncrease"] == {"from": 0, "to": 2}


# ---- purity ----
def test_pure_does_not_mutate_inputs():
    env = _env([_cf("a", "Warning")])
    prev = [_run([_hf("a", "Warning")])]
    import copy
    env_c, prev_c = copy.deepcopy(env), copy.deepcopy(prev)
    decide_alert(env, prev)
    assert env == env_c and prev == prev_c
