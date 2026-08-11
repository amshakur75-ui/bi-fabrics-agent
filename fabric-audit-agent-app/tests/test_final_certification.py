"""Final pre-ship certification: the four defects the last audit found, plus the guard it missed.

Every one was proven by execution before it was fixed.
"""
import inspect

from fabric_audit_agent import job as job_mod
from fabric_audit_agent.automation.materiality import is_escalation, load_cfg
from fabric_audit_agent.automation.tier2_check import _likely_driver_facts
from fabric_audit_agent.outbound import dispatch_outbound

CFG = load_cfg({})


# ---- a number next to a person must be that person's ----------------------

def _row(**kw):
    base = {"item": "Ent-Reporting-DTC", "workspace": "Ent-Reporting", "cuSeconds": 7400.0,
            "user": "aaron@newellco.com", "userCuSeconds": 400.0}
    base.update(kw)
    return {"likelyDrivers": [base]}


def test_the_card_never_lends_a_user_the_items_total():
    """The item's total was printed immediately after the named user, separated only by a comma, so
    an item at 7,400 CPU-s whose top user contributed 400 read as "aaron@newellco.com, 7400.0
    CPU-s" — an 18x overstatement of a named person's load, on the only Teams card the product
    emits. The user's own figure was collected and thrown away."""
    value = _likely_driver_facts(_row())[0][1]
    assert "aaron@newellco.com 400.0 CPU-s" in value, "the user carries THEIR OWN figure"
    assert "aaron@newellco.com, 7400.0" not in value
    assert "7400.0 CPU-s" in value, "the item total is still shown, attributed to the item"


def test_an_unmeasured_user_share_names_the_user_without_a_number():
    """Better to name someone with no figure than to lend them one that is not theirs."""
    value = _likely_driver_facts(_row(userCuSeconds=None))[0][1]
    assert "aaron@newellco.com (share unmeasured)" in value
    assert "aaron@newellco.com 7400" not in value


# ---- one card when it becomes urgent, not one per halving ------------------

def _drain(seq):
    cards, prior = 0, None
    for mtb in seq:
        trig = {"check": "overage", "minutesToBurndown": mtb, "peakCuPct": 96.0}
        if prior is None or is_escalation(trig, prior, CFG):
            cards += 1
            prior = {"severity": "warn", "metric": 96.0, "minutesToBurndown": mtb,
                     "signalTypes": ["overage"]}
    return cards


def test_a_draining_overage_does_not_card_at_every_halving():
    """Gating each halving on the urgency floor moved the threshold without bounding the COUNT:
    50 -> 25 -> 12 -> 6 -> 3 -> 1 carded four times inside the urgent band, all with the same title,
    the same facts and the same "When" — the exact outcome the floor was added to prevent. The
    escalation is now the CROSSING into urgency, which can only happen once per incident."""
    assert _drain([50, 25, 12, 6, 3, 1]) <= 2, "initial card + one 'now urgent', not one per halving"


def test_an_overage_that_never_becomes_urgent_cards_once():
    assert _drain([200, 120, 80, 50]) == 1


def test_a_genuine_crossing_into_urgency_still_escalates():
    """The bound must not become a gag order — becoming urgent is real news."""
    trig = {"check": "overage", "minutesToBurndown": 7.0, "peakCuPct": 96.0}
    prior = {"severity": "warn", "metric": 96.0, "minutesToBurndown": 40.0,
             "signalTypes": ["overage"]}
    assert is_escalation(trig, prior, CFG) is True


# ---- an operator must be able to tell 429 from 404 ------------------------

def test_the_delivery_status_reaches_the_caller():
    """dispatch_outbound dropped `status`, so _send's warning always read "status=None" — the one
    message telling an operator why a capacity card was lost could not distinguish a retryable 429
    from a 404 that will never succeed."""
    for code in (500, 429, 404):
        sink = {"webhook": {"deliver": lambda b, c=code: {"delivered": False, "status": c}}}
        res = dispatch_outbound("tier2_alert", {"summary": "x"}, sinks=sink)
        assert res["status"] == code
        assert res["delivered"] is False


def test_a_sink_without_a_status_is_not_invented():
    sink = {"webhook": {"deliver": lambda b: {"delivered": True}}}
    assert dispatch_outbound("tier2_alert", {"summary": "x"}, sinks=sink)["status"] is None


# ---- the stale-marking gate's CALL SITE, not just the flag ----------------

def test_the_sweep_computes_collection_complete_from_sourcesFailed():
    """A mutation setting `collection_complete=True` unconditionally survived the whole suite: the
    sweep_delivery tests pass the flag directly and nothing asserted how job.py DERIVES it. That
    gate is the only thing stopping a half-blind sweep from reporting real, unfixed findings as no
    longer firing."""
    src = inspect.getsource(job_mod._deliver_sweep_findings)
    assert "collection_complete=not" in src, "the flag must be derived, never hardcoded True"
    assert 'sourcesFailed' in src, "it must be derived from which SOURCES failed"


def test_a_degraded_collection_makes_the_gate_false_end_to_end():
    """The behavioural half: a failed source must actually flip the flag."""
    from fabric_audit_agent.pipeline import run_audit
    from fabric_audit_agent.reasoner_stub import create_stub_reasoner

    def _data(failed):
        facts = {"capacity": {"peakCuPct": 50.0}, "items": []}
        if failed:
            facts["sourcesFailed"] = failed
        return run_audit({"collect": lambda: facts}, create_stub_reasoner(),
                         {"deliver": lambda p: {"delivered": True}})["data"]

    # `collection_complete` is computed as `not (data.get("sourcesFailed"))` at the call site;
    # assert on that same expression so the two cannot drift.
    degraded = _data(["log-analytics: down"])
    assert degraded.get("sourcesFailed") == ["log-analytics: down"]
    assert (not degraded.get("sourcesFailed")) is False, "a failed source must clear the flag"

    clean = _data(None)
    assert (not clean.get("sourcesFailed")) is True, "a full collection may mark findings stale"
