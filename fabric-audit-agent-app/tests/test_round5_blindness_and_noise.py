"""Round 5: blindness that reads as health, and one incident that reads as five.

Each of these ran fine and reported something plausible. That is the whole problem.
"""
from fabric_audit_agent.adapters.attribution_rollup import rollup_attribution
from fabric_audit_agent.automation.materiality import is_escalation, load_cfg
from fabric_audit_agent.automation.tier2_check import (
    _check_silent_failure, _record_reading)
from fabric_audit_agent.context_readings import (
    _FIELDS, create_readings_store_memory)

CFG = load_cfg({})


# ---- a share needs a denominator -------------------------------------------

def _la_row(item, user, cpu=None):
    """A row in the shape the DEPLOYED Log Analytics query produces."""
    r = {"PowerBIWorkspaceName": "Ent", "ArtifactName": item, "ExecutingUser": user}
    if cpu is not None:
        r["cpuMs"] = cpu
    return r


def test_a_missing_cost_column_yields_an_unknown_share_not_a_confident_zero():
    """If no row resolves a cost — a schema rename, a changed alias, a frequency-only source —
    `total` is 0 and `(cpu / total * 100) if total else 0` handed EVERY item a sharePct of 0. The
    headline 30% concentration feature went permanently quiet while emitting a normal-looking
    payload, and nothing could tell that from "no item is responsible for any load"."""
    facts = rollup_attribution([_la_row("Ent-Reporting-DTC", "carol@co"),
                                _la_row("Ent-Reporting-Sales", "bob@co")])
    assert facts["items"], "the items themselves must still be reported"
    for it in facts["items"]:
        assert it["sharePct"] is None, "an uncomputable share must be unknown, not zero"
        assert it["shareBasis"] == "unavailable"
    for u in facts["users"]:
        assert u["sharePct"] is None


def test_a_present_cost_column_still_produces_real_shares():
    facts = rollup_attribution([_la_row("Ent-Reporting-DTC", "carol@co", 3_000_000),
                                _la_row("Ent-Reporting-Sales", "bob@co", 1_000_000)])
    by_name = {i["name"]: i for i in facts["items"]}
    assert round(by_name["Ent-Reporting-DTC"]["sharePct"]) == 75
    assert by_name["Ent-Reporting-DTC"]["shareBasis"] == "cost"
    # cpuMs IS sum(CpuTimeMs) under an alias in the deployed query, so this is TRUE cpu.
    assert by_name["Ent-Reporting-DTC"]["attributionMode"] == "cost-cpu"


# ---- capacity blindness must not read as health ----------------------------

def _reading(ok=True, cap_ok=True):
    return {"collectorOk": ok, "capacityOk": cap_ok, "peakCuPct": 50.0 if cap_ok else None}


def test_a_dead_capacity_source_is_detected_even_while_attribution_flows():
    """collectorOk is an OR across sources. An Eventhouse returning zero rows WITHOUT raising, while
    Log Analytics attribution keeps flowing, left collectorOk True forever — so every CU threshold
    and both CU-based trend gates were blind, no capacity alert could ever fire again, and the
    detector whose whole job is noticing that saw a healthy collector."""
    readings = [_reading(ok=True, cap_ok=False) for _ in range(4)]
    trigs = _check_silent_failure(readings, CFG)
    assert len(trigs) == 1
    assert trigs[0]["blindSource"] == "capacity"
    assert "CAPACITY source" in trigs[0]["normalityHint"]


def test_a_fully_dead_collector_still_reports_the_general_blindness():
    trigs = _check_silent_failure([_reading(ok=False, cap_ok=False) for _ in range(4)], CFG)
    assert len(trigs) == 1 and trigs[0].get("blindSource") is None


def test_a_healthy_window_is_silent():
    assert _check_silent_failure([_reading() for _ in range(4)], CFG) == []


def test_readings_predating_the_capacity_flag_are_not_treated_as_failures():
    """`None` means the column predates this check, not that capacity was absent — otherwise the
    first run after deploy would alarm on its own history."""
    old = [{"collectorOk": True, "capacityOk": None, "peakCuPct": 50.0} for _ in range(4)]
    assert _check_silent_failure(old, CFG) == []


def test_the_recorded_reading_carries_the_capacity_flag():
    """FIELD-MAPPING DISCIPLINE: a key absent from _FIELDS is silently dropped on the Delta path.
    That exact omission was a P0 in this repo."""
    assert "capacityOk" in {cc for cc, _col in _FIELDS}
    store = create_readings_store_memory()
    _record_reading(store, run_at="2026-08-10T12:00:00Z",
                    facts={"capacity": {}, "items": [{"name": "x"}]}, collector_ok=True)
    assert store["_data"][0]["capacityOk"] is False      # items flowed, capacity did not
    _record_reading(store, run_at="2026-08-10T12:05:00Z",
                    facts={"capacity": {"peakCuPct": 71.0}, "items": []}, collector_ok=True)
    assert store["_data"][1]["capacityOk"] is True


# ---- one draining incident is one incident ---------------------------------

def test_a_halving_far_from_urgency_is_not_an_escalation():
    """The still-firing branch deliberately does not upsert, so prior.minutesToBurndown only advances
    when a card is SENT. One overage draining 50 -> 2 therefore halved repeatedly and carded at 50,
    25, 12, 6 and 3: five Teams cards, same title, same facts, same "When"."""
    trigger = {"check": "overage", "minutesToBurndown": 25.0, "peakCuPct": 118.0}
    prior = {"severity": "warn", "metric": 118.0, "minutesToBurndown": 50.0,
             "signalTypes": ["overage"]}
    assert is_escalation(trigger, prior, CFG) is False


def test_a_halving_that_crosses_into_urgency_is_an_escalation():
    trigger = {"check": "overage", "minutesToBurndown": 6.0, "peakCuPct": 118.0}
    prior = {"severity": "warn", "metric": 118.0, "minutesToBurndown": 50.0,
             "signalTypes": ["overage"]}
    assert is_escalation(trigger, prior, CFG) is True


def test_reaching_urgency_without_halving_is_not_an_escalation():
    """Both halves are required: a slow drift into the urgent band is not new information."""
    trigger = {"check": "overage", "minutesToBurndown": 14.0, "peakCuPct": 118.0}
    prior = {"severity": "warn", "metric": 118.0, "minutesToBurndown": 15.0,
             "signalTypes": ["overage"]}
    assert is_escalation(trigger, prior, CFG) is False


# ---- a card that never arrived must not be recorded as delivered -----------

def test_a_failed_teams_card_is_recorded_and_retried_next_tick():
    """The single worst hole in the alerting path, and it went MISSING once already.

    The webhook sink returns {"delivered": False, "status": 500} rather than raising, so callers
    assigned that into row["delivered"], the row went active, and the next tick took the
    "already active, stay silent" branch — losing a capacity emergency's card FOREVER with no
    exception, no log, no health record, and a green job.

    This test exists because a round-3 commit MESSAGE claimed this fix while the diff contained only
    an unrelated change: the edit had been silently reverted by a subagent's worktree restore, and I
    repaired only the damage visible in that diff rather than re-checking every edit in the commit.
    A commit message is not evidence. A test is.
    """
    from datetime import datetime, timedelta, timezone

    from fabric_audit_agent.automation.health import HealthReport
    from fabric_audit_agent.automation.tier2_check import process_alerts
    from fabric_audit_agent.context_alerts import create_alerts_store_memory

    t0 = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    store = create_alerts_store_memory()
    health = HealthReport()
    attempts = []

    def failing(body):
        attempts.append(body)
        return {"delivered": False, "status": 500}

    trig = [{"check": "throttle", "throttleMinutes": 8.5, "peakCuPct": 210.0},
            {"check": "pressure", "peakCuPct": 210.0}]
    kw = dict(alerts_store=store, delivery_sinks={"webhook": {"deliver": failing}},
              reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True}, health=health)

    process_alerts(trig, now_dt=t0, **kw)
    assert len(attempts) == 1, "the first card must be attempted"
    row = store["_data"]["capacity::capacity"]
    assert row["delivered"] is False, "a 500 must not be recorded as delivered"
    assert health.degraded is True and "NOT delivered" in health.summary

    # Still firing, still never told -> retry rather than the silent already-active branch.
    process_alerts(trig, now_dt=t0 + timedelta(minutes=5), **kw)
    assert len(attempts) == 2, "a card that never landed must be retried while the incident fires"


def test_a_delivered_card_is_not_retried():
    """The retry must not become a card-every-tick bug for the healthy case."""
    from datetime import datetime, timedelta, timezone

    from fabric_audit_agent.automation.tier2_check import process_alerts
    from fabric_audit_agent.context_alerts import create_alerts_store_memory

    t0 = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    store = create_alerts_store_memory()
    attempts = []

    def ok(body):
        attempts.append(body)
        return {"delivered": True, "status": 200}

    trig = [{"check": "throttle", "throttleMinutes": 8.5, "peakCuPct": 210.0},
            {"check": "pressure", "peakCuPct": 210.0}]
    kw = dict(alerts_store=store, delivery_sinks={"webhook": {"deliver": ok}},
              reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True})
    process_alerts(trig, now_dt=t0, **kw)
    process_alerts(trig, now_dt=t0 + timedelta(minutes=5), **kw)
    assert len(attempts) == 1, "a still-firing incident that WAS delivered must stay silent"


# ---- the guards that were silently reverted once already --------------------

def test_the_blindness_detector_can_reach_teams():
    """silent_failure is a META-alarm about the alerting pipeline, not attribution noise. Left out of
    _TEAMS_CHECKS, the one alarm saying "this agent has stopped working" was reachable only by
    opening the app — the failure mode hid the alarm for the failure mode."""
    import inspect
    import re

    from fabric_audit_agent.automation import tier2_check
    src = inspect.getsource(tier2_check.process_alerts)
    m = re.search(r"_TEAMS_CHECKS = \((.*?)\)", src, re.DOTALL)
    assert m, "could not locate _TEAMS_CHECKS"
    assert "silent_failure" in set(re.findall(r'"([^"]+)"', m.group(1)))


def test_triggers_with_nowhere_to_go_are_recorded_not_swallowed():
    """Alerting silently OFF produced `delivered={}` — byte-identical to a healthy quiet run — so a
    fully de-wired alerting path reported green forever. Preflight checks none of the alerting env."""
    from fabric_audit_agent.automation.health import HealthReport
    from fabric_audit_agent.automation.tier2_check import run_tier2_check

    health = HealthReport()
    collector = {"collect": lambda: {"capacity": {"peakCuPct": 210.0, "throttleMinutes": 9.0},
                                     "items": []}}
    # No delivery sinks and no alerts store: the incident is real and has nowhere to go.
    out = run_tier2_check(collector, delivery_sinks=None, health=health)
    assert out["triggered"] is True, "the incident itself must still be detected"
    assert health.degraded is True
    assert "alerting NOT wired" in health.summary
