"""The materiality gate has THREE tiers, and the middle one must actually exist in production.

`classify` returns report / ambiguous / suppress. In production the deployed v1 reasoner
(`job._build_tier2_reasoner`) is a deterministic facts renderer that ends with a hardcoded
``"report": True``, and `process_alerts` used to compute
``report = decision == "report" or bool(inv["report"])`` — so **ambiguous was identical to
report**, and every sub-threshold blip pushed a Teams card. A single 30-second window touching
100.4% CU cards as "Capacity incident (throttling + CU pressure)", which is exactly the noise the
middle tier was built to stop.

An ambiguous trigger must be RECORDED (notification center + daily digest) and must not PAGE.
Only a reasoner that declares ``judged: True`` — a real LLM verdict, not the facts renderer — may
promote it.
"""
from datetime import datetime, timezone

from fabric_audit_agent.automation.tier2_check import process_alerts
from fabric_audit_agent.context_alerts import create_alerts_store_memory
from fabric_audit_agent.job import _build_tier2_reasoner

T0 = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _v1_reasoner():
    """The reasoner EXACTLY as the deployed tier2 job builds it (job.run_tier2_job)."""
    return _build_tier2_reasoner({}, {})


def _sink():
    posts = []
    return posts, {"deliver": lambda body: (posts.append(body), {"delivered": True})[1]}


def _run(trigs, reasoner=None, store=None):
    store = store if store is not None else create_alerts_store_memory()
    posts, sink = _sink()
    acts = process_alerts(trigs, alerts_store=store, delivery_sinks={"webhook": sink},
                          reasoner=reasoner, now_dt=T0)
    return acts, posts, store


# ---- the production reasoner is the whole point ---------------------------

def test_deployed_v1_reasoner_does_not_claim_to_be_a_judgement():
    """If this ever starts returning judged=True without a real LLM behind it, the middle tier
    silently disappears again. This is the guard on the guard."""
    inv = _v1_reasoner()({"check": "pressure", "peakCuPct": 110.0})
    assert inv.get("judged") is not True


def test_ambiguous_with_the_production_reasoner_records_but_does_not_page():
    """The live regression: 110% peak is `ambiguous` (105-119.9 band). Before the fix this
    produced a Teams card because the v1 reasoner said report=True."""
    acts, posts, store = _run([{"check": "pressure", "peakCuPct": 110.0}],
                              reasoner=_v1_reasoner())
    assert posts == [], "an ambiguous trigger must not page anyone"
    assert acts["informational"] == ["capacity::capacity"]
    assert acts["new"] == []
    row = store["_data"]["capacity::capacity"]
    assert row["status"] == "informational"      # recorded, so the digest + centre still see it
    assert row["currentlyActive"] is True


def test_the_30_second_blip_that_started_all_this_no_longer_cards():
    """One 30-s window at 100.4%: throttleMinutes 0.5 (under the 2.5 bar) and peak 100.4 (under
    the 105 bar). Both components ambiguous -> composite ambiguous -> no card."""
    acts, posts, _store = _run(
        [{"check": "throttle", "throttleMinutes": 0.5, "peakCuPct": 100.4},
         {"check": "pressure", "peakCuPct": 100.4}],
        reasoner=_v1_reasoner())
    assert posts == []
    assert acts["informational"] == ["capacity::capacity"]


def test_report_tier_still_pages_immediately():
    """The middle tier must not become a gag order on genuine incidents."""
    acts, posts, _store = _run(
        [{"check": "throttle", "throttleMinutes": 8.5, "peakCuPct": 210.0},
         {"check": "pressure", "peakCuPct": 210.0}],
        reasoner=_v1_reasoner())
    assert len(posts) == 1
    assert acts["new"] == ["capacity::capacity"]


# ---- a REAL verdict is still honoured, in both directions ----------------

def test_a_declared_verdict_may_promote_an_ambiguous_trigger():
    acts, posts, _store = _run(
        [{"check": "pressure", "peakCuPct": 110.0}],
        reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True, "judged": True})
    assert len(posts) == 1 and acts["new"] == ["capacity::capacity"]


def test_a_declared_verdict_may_silence_an_ambiguous_trigger():
    acts, posts, _store = _run(
        [{"check": "pressure", "peakCuPct": 110.0}],
        reasoner=lambda t: {"markdown": "m", "summary": "s", "report": False, "judged": True})
    assert posts == [] and acts["silent"] == ["capacity::capacity"]


def test_an_ambiguous_trigger_is_never_simply_dropped():
    """Whatever the routing, the incident must land SOMEWHERE. "Absence of data is not absence of
    problems" applies to our own suppression decisions too."""
    for reasoner in (None, _v1_reasoner()):
        acts, _posts, _store = _run([{"check": "pressure", "peakCuPct": 110.0}], reasoner=reasoner)
        landed = acts["new"] + acts["informational"] + acts["silent"] + acts["pending"]
        assert "capacity::capacity" in landed
