from fabric_audit_agent.automation.dedupe import dedupe
from fabric_audit_agent.automation.escalate import apply_escalation
from fabric_audit_agent.automation.trend import annotate_recurring
from fabric_audit_agent.automation.digest import build_digest


def _f(key, level="Warning", reason="r", **extra):
    d = {"key": key, "score": {"level": level, "reason": reason}}
    d.update(extra)
    return d


# ---- dedupe ----
def test_dedupe_by_key_keeps_keyless():
    out = dedupe([_f("a"), _f("a"), {"what": "no key"}, _f("b")])
    keys = [f.get("key") for f in out]
    assert keys.count("a") == 1 and "b" in keys
    assert sum(1 for f in out if "key" not in f) == 1   # keyless kept


# ---- escalate ----
def test_escalate_warning_to_critical_when_present_in_last_two_runs():
    history = [{"runAt": "1", "findings": [{"key": "x"}]}, {"runAt": "2", "findings": [{"key": "x"}]}]
    out = apply_escalation([_f("x", "Warning")], history)
    assert out[0]["score"]["level"] == "Critical" and "escalated" in out[0]["score"]["reason"]


def test_escalate_noop_with_fewer_than_two_runs():
    assert apply_escalation([_f("x", "Warning")], [{"findings": [{"key": "x"}]}])[0]["score"]["level"] == "Warning"


def test_escalate_skips_when_not_in_both_runs():
    history = [{"findings": [{"key": "x"}]}, {"findings": [{"key": "y"}]}]
    assert apply_escalation([_f("x", "Warning")], history)[0]["score"]["level"] == "Warning"


# ---- trend ----
def test_annotate_recurring_counts_window():
    history = [{"findings": [{"key": "x"}]}, {"findings": [{"key": "x"}]}, {"findings": [{"key": "y"}]}]
    by_key = {f["key"]: f["recurringRuns"] for f in annotate_recurring([_f("x"), _f("z")], history)}
    assert by_key["x"] == 3   # 2 prior + current
    assert by_key["z"] == 1   # current only


# ---- digest ----
def test_build_digest_totals_new_and_recurring():
    findings = [_f("capacity.throttle::a", "Critical", recurringRuns=4), _f("model.bidirectional::b", "Warning", recurringRuns=1)]
    d = build_digest(findings, [{"findings": [{"key": "capacity.throttle::a"}]}])   # b is new
    assert d["totals"]["Critical"] == 1 and d["totals"]["Warning"] == 1
    assert d["byDomain"]["capacity"] == 1 and d["byDomain"]["model"] == 1
    assert d["newCount"] == 1
    assert [r["key"] for r in d["recurring"]] == ["capacity.throttle::a"]   # recurringRuns 4 >= 3
