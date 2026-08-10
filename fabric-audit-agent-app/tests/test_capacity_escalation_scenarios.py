"""End-to-end escalation SEMANTICS — "would a human want a card here?"

Every test drives the real ``process_alerts`` through a multi-tick sequence against a
Delta-FAITHFUL store (the real ``_to_row``/``_from_row``), and asserts the CARD COUNT plus the
stored high-water state. These are the sequences an audit round kept getting wrong by inspection,
so they are pinned here rather than reasoned about.

The product promise being tested: ONE card per underlying capacity incident; a genuine WORSENING
breaks through; an IMPROVEMENT never does; quiet when nothing is wrong.
"""
from datetime import datetime, timedelta, timezone

from fabric_audit_agent.automation.tier2_check import process_alerts
from fabric_audit_agent.automation.materiality import load_cfg
from tests.test_alerts_store_delta_fidelity import create_delta_faithful_store

T0 = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _sink():
    posts = []
    return posts, {"deliver": lambda b: (posts.append(b), {"delivered": True, "status": 202})[1]}


def _kw(store, sink, **over):
    cfg = load_cfg()
    cfg["hysteresis_ticks"] = 1
    cfg.update(over.pop("cfg", {}))
    return dict(alerts_store=store, delivery_sinks={"webhook": sink},
                reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True},
                chat_writer=lambda m, t: "c1", app_url="https://app", cfg=cfg, **over)


def _run(store, sink, seq, **over):
    """Drive one trigger-list per tick, 5 minutes apart. Returns (posts, actions_per_tick)."""
    kw = _kw(store, sink, **over)
    acts = []
    for i, trigs in enumerate(seq):
        acts.append(process_alerts(list(trigs), now_dt=T0 + timedelta(minutes=5 * i), **kw))
    return acts


def _throttle(minutes, peak):
    return {"check": "throttle", "throttleMinutes": minutes, "peakCuPct": peak}


def _pressure(peak):
    return {"check": "pressure", "peakCuPct": peak}


def _overage(burndown, peak=105.0):
    return {"check": "overage", "overageTotalMs": 9000.0, "minutesToBurndown": burndown,
            "overageCumulativePct": 12.0, "peakCuPct": peak}


# --- S1: an IMPROVEMENT must not alert ---------------------------------------------------

def test_improvement_does_not_fire_a_card_and_does_not_downgrade_the_ticket():
    """THE round-1 HIGH. Throttling STOPS and CU falls below 100 while Fabric's threshold pcts
    stay elevated: the signal set ROTATES {pressure,throttle} -> {throttle_imminent}. Under
    set-difference that produced an "escalation" card for an event that had just got BETTER, and
    rewrote the row warn->info / 105->99, losing the incident's high-water mark."""
    store = create_delta_faithful_store()
    posts, sink = _sink()
    acts = _run(store, sink, [
        [_throttle(6.0, 105.0), _pressure(105.0)],   # tick 1: real incident -> 1 card
        [_overage(300.0, 99.0)],                     # tick 2: IMPROVED -> must be silent
        [_throttle(6.0, 105.0), _pressure(105.0)],   # tick 3: back to the same state -> silent
    ])
    assert len(posts) == 1, f"an improvement fired a card ({len(posts)} total)"
    assert acts[1]["escalation"] == [], "tick 2 was treated as an escalation"
    row = store["_data"]["capacity::capacity"]
    # High-water marks held: severity did not fall back to info and metric did not fall to 99.
    assert row["severity"] == "warn"
    assert row["metric"] == 105.0
    # The stored set is the last ALERTED state, NOT the last observed one: the still-firing
    # `silent` branch deliberately does not upsert (that invariant is what keeps a flapping set
    # from looking like "a new signal joined" on every oscillation). So `throttle_imminent`,
    # which only ever appeared on a silent tick, is correctly absent.
    assert set(row["signalTypes"]) == {"throttle", "pressure"}


def test_an_equally_severe_signal_joining_IS_an_escalation():
    """Counterpart to the improvement case. extreme_peak (a >=200% spike) joining a continuing
    throttle+pressure incident genuinely grew the set with a signal at least as severe as the
    worst already seen, so it must break through."""
    store = create_delta_faithful_store()
    posts, sink = _sink()
    acts = _run(store, sink, [
        [_throttle(6.0, 150.0), _pressure(150.0)],
        [_throttle(6.0, 210.0), _pressure(210.0),
         {"check": "extreme_peak", "peakCuPct": 210.0, "extremeThreshold": 200.0}],
    ])
    assert len(posts) == 2
    assert acts[1]["escalation"] == ["capacity::capacity"]
    assert set(store["_data"]["capacity::capacity"]["signalTypes"]) == {
        "throttle", "pressure", "extreme_peak"}


# --- S2: genuine worsenings must break through -------------------------------------------

def test_peak_jump_then_throttle_start_each_fire_once():
    store = create_delta_faithful_store()
    posts, sink = _sink()
    acts = _run(store, sink, [
        [_pressure(110.0)],                          # new incident -> card 1
        [_pressure(140.0)],                          # +30 peak -> card 2
        [_pressure(140.0), _throttle(6.0, 140.0)],   # throttling STARTS -> card 3
        [_pressure(140.0), _throttle(6.0, 140.0)],   # unchanged -> silent
    ])
    assert len(posts) == 3
    assert acts[1]["escalation"] == ["capacity::capacity"]
    assert acts[2]["escalation"] == ["capacity::capacity"]
    assert acts[3]["silent"] == ["capacity::capacity"]


def test_a_thirty_minute_throttle_after_a_huge_peak_is_not_silent():
    """The unit-mixing bug's worst symptom: `30 >= max(5, 2*250)` was False, so a real
    30-minute throttle after a 250% peak produced NOTHING."""
    store = create_delta_faithful_store()
    posts, sink = _sink()
    _run(store, sink, [
        [_pressure(250.0)],                          # card 1 (also extreme? no — detector-level)
        [_pressure(240.0), _throttle(30.0, 240.0)],  # throttling starts -> MUST card
    ])
    assert len(posts) == 2


# --- S3: burndown collapse is the fourth axis --------------------------------------------

def test_burndown_collapse_fires_once_when_it_halves():
    """Peak flat, throttle flat, signal set already the union -> the other three axes are blind.
    Overage draining 50min -> 2min is an imminent worsening and must break through."""
    store = create_delta_faithful_store()
    posts, sink = _sink()
    acts = _run(store, sink, [
        [_overage(50.0)],    # new incident -> card 1
        [_overage(45.0)],    # -10%, not a halving -> silent
        [_overage(20.0)],    # <= 45/2 -> card 2
        [_overage(19.0)],    # not <= 20/2 -> silent
        [_overage(2.0)],     # <= 19/2 -> card 3
    ])
    assert len(posts) == 3
    assert acts[1]["silent"] == ["capacity::capacity"]
    assert acts[3]["silent"] == ["capacity::capacity"]


# --- S4: a narrowing then re-widening set must not re-fire -------------------------------

def test_signal_set_returning_to_a_previously_seen_state_is_silent():
    """{throttle,pressure} -> +extreme_peak (worse, cards) -> back to {throttle,pressure}
    (better). The return must NOT card: the stored set is the union, so nothing is new."""
    store = create_delta_faithful_store()
    posts, sink = _sink()
    acts = _run(store, sink, [
        [_throttle(6.0, 150.0), _pressure(150.0)],
        [_throttle(6.0, 210.0), _pressure(210.0),
         {"check": "extreme_peak", "peakCuPct": 210.0, "extremeThreshold": 200.0}],
        [_throttle(6.0, 150.0), _pressure(150.0)],
    ])
    assert len(posts) == 2, "returning to a previously-seen signal set re-fired"
    assert acts[2]["silent"] == ["capacity::capacity"]


def test_flapping_signal_set_produces_exactly_one_card():
    """A oscillates in and out over many ticks. The still-firing `silent` branch must not
    refresh the row (that invariant is what keeps the stored set wide)."""
    store = create_delta_faithful_store()
    posts, sink = _sink()
    wide = [_throttle(6.0, 150.0), _pressure(150.0)]
    narrow = [_pressure(150.0)]
    _run(store, sink, [wide, narrow, wide, narrow, wide, narrow, wide])
    assert len(posts) == 1


# --- high-water marks must not become a gag order ---------------------------------------

def test_metric_high_water_still_allows_a_later_genuine_escalation():
    """peak 250 -> 100 -> 130. The 130 must NOT escalate (it is below the 250 high-water, i.e.
    not worse than this incident has been) — but a jump ABOVE the high-water must."""
    store = create_delta_faithful_store()
    posts, sink = _sink()
    acts = _run(store, sink, [
        [_pressure(250.0)],   # card 1, metric high-water 250
        [_pressure(100.0)],   # improved -> silent
        [_pressure(130.0)],   # still below the high-water -> silent, NOT a new alert
        [_pressure(275.0)],   # +25 over the 250 high-water -> card 2
    ])
    assert len(posts) == 2
    assert acts[2]["silent"] == ["capacity::capacity"]
    assert acts[3]["escalation"] == ["capacity::capacity"]
    assert store["_data"]["capacity::capacity"]["metric"] == 275.0


def test_incident_can_still_escalate_after_many_quiet_ticks_within_grace():
    """absenceCount accrues but the incident is NOT resolved; a worsening on return still cards
    exactly once, and the grace clock resets."""
    store = create_delta_faithful_store()
    posts, sink = _sink()
    seq = [[_pressure(110.0)]] + [[] for _ in range(5)] + [[_pressure(160.0)]]
    acts = _run(store, sink, seq, cfg={"quiet_ticks": 12})
    assert len(posts) == 2
    row = store["_data"]["capacity::capacity"]
    assert row["absenceCount"] == 0 and row["currentlyActive"] is True
    assert acts[-1]["escalation"] == ["capacity::capacity"]


# --- quiet when nothing is wrong ---------------------------------------------------------

def test_a_completely_healthy_estate_never_cards():
    store = create_delta_faithful_store()
    posts, sink = _sink()
    _run(store, sink, [[] for _ in range(20)])
    assert posts == []
    assert store["_data"] == {}
