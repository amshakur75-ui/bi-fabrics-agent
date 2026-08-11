"""Every alert, driven end-to-end through the real sweep, with nothing stubbed but I/O.

The suite has deep unit coverage of each detector and of `process_alerts` in isolation. What it did
not have is a single harness that starts from a facts dict a collector could actually return, runs
`run_tier2_check` with real (in-memory) stores, and asserts what a HUMAN ends up seeing: a Teams
card, a notification-center ticket, or deliberate silence. That is the level the product promise
lives at, and it is the level at which three separate defects have hidden behind green unit tests
(a decorative `data_quality=` argument, an unreachable `collection_complete` gate, and a card that
attributed an item's whole cost to one named person).

Every scenario is run TWICE against a fresh store set, asserting identical routing, because several
of these paths carry state and "works once" is not the claim being made.
"""
from datetime import datetime, timedelta, timezone

import pytest

from fabric_audit_agent.automation.tier2_check import run_tier2_check
from fabric_audit_agent.context_alerts import create_alerts_store_memory
from fabric_audit_agent.context_readings import create_readings_store_memory

TEAMS_ONLY = ("throttle", "pressure", "overage", "extreme_peak", "capacity_incident",
              "silent_failure")


def _stores():
    """The product's OWN in-memory stores, not hand-rolled doubles.

    The first draft of this file hand-rolled the alerts and readings ports and got both shapes
    subtly wrong, so the delivery pass died inside its own try/except and every scenario reported
    "no triggers" -- a green-looking harness measuring nothing. Using the shipped memory adapters
    means a shape change in the real port shows up here as a failure instead of as silence.
    """
    sent, tickets, chats = [], [], []

    def deliver(payload):
        sent.append(payload)
        return {"delivered": True, "status": 200}

    def write_ticket(*a, **kw):
        tickets.append((a, kw))
        return "ticket-1"

    def write_chat(*a, **kw):
        chats.append((a, kw))
        return "chat-1"

    return {
        "alerts": create_alerts_store_memory(),
        "readings": create_readings_store_memory(),
        "sinks": {"webhook": {"deliver": deliver}},
        "sent": sent,
        "tickets": tickets,
        "chats": chats,
        "ticket_writer": write_ticket,
        "chat_writer": write_chat,
    }


_T0 = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)


def _run(facts, st=None, **kw):
    """One sweep. Consecutive sweeps on the same store set are five minutes apart, matching the
    deployed cron -- readings are ordered BY TIMESTAMP, so hammering the same instant is not a
    faster version of the real thing, it is a different scenario."""
    st = st or _stores()
    if "now_dt" not in kw:
        st["tick"] = st.get("tick", 0) + 1
        kw["now_dt"] = _T0 + timedelta(minutes=5 * st["tick"])
    res = run_tier2_check({"collect": lambda: facts}, alerts_store=st["alerts"],
                          delivery_sinks=st["sinks"], ticket_writer=st["ticket_writer"],
                          chat_writer=st["chat_writer"], readings_store=st["readings"],
                          app_url="https://app.example", **kw)
    return res, st


def _checks(res):
    return sorted({t["check"] for t in res["triggers"]})


def _fired(res):
    return [t for t in res["triggers"] if t.get("check") != "data_unavailable"]


# ---- facts in the shape the deployed collector emits -------------------------

def _facts(peak=40.0, throttle=0.0, items=None, **cap):
    c = {"capacityId": "cap-1", "peakCuPct": peak, "throttleMinutes": throttle,
         "windowLabel": "last 5 minutes"}
    c.update(cap)
    return {"capacity": c, "items": items if items is not None else []}


def _item(name="Ent-Reporting-DTC", ws="Ent-Reporting", share=None, cu=None, users=None):
    users = users or []
    return {"name": name, "workspace": ws, "sharePct": share, "cuSeconds": cu,
            "topUsers": users, "attributionMode": "cost-cpu",
            "shareBasis": "cost" if cu is not None else "unavailable",
            "userCount": len(users), "truncated": False}


# name -> (facts, raw checks that MUST fire, number of Teams cards a human receives)
#
# The card count is the assertion that matters. `res["triggers"]` is the RAW, pre-coalesce list;
# the Design A' composite (`capacity::<id>`) is formed inside process_alerts, which is exactly why
# four raw capacity signals must still produce ONE card.
SCENARIOS = {
    "quiet_estate": (_facts(peak=22.0), set(), 0),
    # peak >= 70 with no attribution also raises blind_spot ("capacity is hot and we cannot see
    # who"), which is correct and center-only.
    "over_100_cu": (_facts(peak=140.0, throttle=6.0), {"throttle", "pressure"}, 1),
    "extreme_peak": (_facts(peak=460.0, throttle=30.0),
                     {"throttle", "pressure", "extreme_peak"}, 1),
    # pressure is "over 100% CU without a confirmed throttle", so it needs >100, not merely high --
    # and it must clear the pressure_report floor (120%) to be worth a Teams card. See the
    # boundary test below for the suppressed side.
    "pressure_only": (_facts(peak=125.0), {"pressure"}, 1),
    "overage_burndown": (_facts(peak=118.0, overageTotalMs=84600000.0,
                                minutesToBurndown=44.0, overageCumulativePct=12.0),
                         {"overage", "pressure"}, 1),
    "concentration": (_facts(peak=88.0, items=[
        _item(share=64.0, cu=4200.0,
              users=[{"user": "aaron@newellco.com", "cuSeconds": 3600.0}])]),
        {"concentration"}, 0),
    "cross_user": (_facts(peak=86.0, items=[
        _item(share=58.0, cu=5000.0, users=[{"user": "a@newellco.com", "cuSeconds": 1700.0},
                                            {"user": "b@newellco.com", "cuSeconds": 1650.0},
                                            {"user": "c@newellco.com", "cuSeconds": 1600.0}])]),
        {"concentration", "cross_user"}, 0),
    # One blind sweep is deliberately silent: an empty capacity payload is not yet evidence of
    # anything. Three consecutive blind sweeps raise silent_failure — see the meta-alarm tests.
    "blind_sweep": ({"capacity": {}, "items": []}, set(), 0),
}


# ---- one scenario per alert, each asserted twice -----------------------------

@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("pass_no", (1, 2))
def test_scenario_is_deterministic_and_routes_correctly(name, pass_no):
    facts, expected, cards = SCENARIOS[name]
    res, st = _run(facts)

    missing = expected - set(_checks(res))
    assert not missing, f"{name}: expected {sorted(missing)} to fire, got {_checks(res)}"

    assert len(st["sent"]) == cards, (
        f"{name}: expected {cards} Teams card(s), got {len(st['sent'])}. "
        f"raw checks={_checks(res)}")

    if not _fired(res):
        assert st["sent"] == [], f"{name}: silence must mean silence, not a quiet card"


def test_a_brief_dip_over_100_percent_is_not_a_teams_emergency():
    """pressure_suppress is 105%: one 5-minute window at 103% with no throttle is a fact for the
    notification center, not a card that interrupts someone. The floor exists because this estate
    crosses 100% routinely; carding every crossing is what made the old alerting unreadable."""
    res, st = _run(_facts(peak=103.0))
    assert "pressure" in _checks(res), "the signal must still be DETECTED and recorded"
    assert st["sent"] == [], "a 103% blip must not reach Teams"


def test_the_same_signal_above_the_reporting_floor_does_card():
    """The other side of the boundary — the floor must not be a gag order."""
    _res, st = _run(_facts(peak=125.0))
    assert len(st["sent"]) == 1


def test_four_raw_capacity_signals_still_produce_one_card():
    """Design A': the coalesce is the whole reason a bad minute is not four notifications."""
    res, st = _run(SCENARIOS["extreme_peak"][0])
    capacity_raw = [t for t in res["triggers"]
                    if t["check"] in ("throttle", "pressure", "extreme_peak", "overage")]
    assert len(capacity_raw) >= 3, "fixture no longer produces a multi-signal window"
    assert len(st["sent"]) == 1
    assert any(k.startswith("capacity::") for k in (res["delivered"] or {}).get("new", [])), (
        "the delivered incident must be the composite, not one of the components")


# ---- properties that must hold on EVERY card, whatever the scenario ---------

@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_no_card_presents_the_proxy_as_billed_capacity_cu(name):
    """CpuTimeMs/DurationMs attribution is a PROXY for capacity CU. Presenting it as billed CU is
    the claim investigation/gates.py marks permanently blocked."""
    _res, st = _run(SCENARIOS[name][0])
    for payload in st["sent"]:
        blob = str(payload).lower()
        if "cpu-s" in blob or "cu-s" in blob:
            assert ("monitored" in blob or "not billed" in blob or "proxy" in blob), (
                f"{name}: a card showed a cost figure with no proxy disclosure")


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_no_card_calls_minutes_over_100_percent_a_throttle(name):
    """throttleMinutes is minutes at CU >= 100%, NOT a throttling signal. A card that calls it
    throttling tells an operator the capacity was rejecting requests when it may not have been."""
    _res, st = _run(SCENARIOS[name][0])
    for payload in st["sent"]:
        blob = str(payload)
        if "throttl" in blob.lower():
            assert "100" in blob, (
                f"{name}: card says throttling with no reference to the 100% CU threshold: "
                f"{blob[:300]}")


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_delivered_card_links_back_to_the_app(name):
    _res, st = _run(SCENARIOS[name][0])
    for payload in st["sent"]:
        assert "https://app.example" in str(payload), f"{name}: card has no link back to the app"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_a_still_firing_incident_never_re_notifies(name):
    """The central noise promise: an unchanged incident produces one card, ever. Repeating a card
    every N minutes is exactly the noise the product was asked to remove."""
    st = _stores()
    for _ in range(4):
        _run(SCENARIOS[name][0], st=st)
    assert len(st["sent"]) <= 1, (
        f"{name}: four identical sweeps sent {len(st['sent'])} cards")


# ---- the alarm for when the agent itself goes blind -------------------------

def test_a_blind_collector_raises_the_meta_alarm_and_it_reaches_teams():
    """silent_failure is the one alarm that says "this agent has stopped working". Center-only
    routing would let the failure mode hide the alarm for the failure mode."""
    st = _stores()
    res = None
    for _ in range(8):
        res, _ = _run({"capacity": {}, "items": []}, st=st)
    assert "silent_failure" in _checks(res), (
        f"eight blind sweeps did not raise the blindness alarm: {_checks(res)}")
    assert st["sent"], "the blindness alarm must reach Teams, not only the notification center"


def test_a_recovered_collector_stops_raising_the_meta_alarm():
    """No latching: once data returns, the alarm must clear on its own."""
    st = _stores()
    for _ in range(8):
        _run({"capacity": {}, "items": []}, st=st)
    # The detector looks at a rolling window, so recovery is not instantaneous by design -- one good
    # reading after eight blind ones leaves the window mostly blind. What must not happen is a LATCH:
    # once the window is clean the alarm has to go away on its own.
    res = None
    for _ in range(4):
        res, _ = _run(_facts(peak=30.0), st=st)
    assert "silent_failure" not in _checks(res), "the blindness alarm latched after recovery"


# ---- a real incident must name a real person, correctly ---------------------

def test_the_capacity_card_names_the_heaviest_user_with_that_users_own_cost():
    facts = _facts(peak=210.0, throttle=12.0, items=[
        _item(share=64.0, cu=7400.0, users=[{"user": "aaron@newellco.com", "cuSeconds": 400.0},
                                            {"user": "bipin@newellco.com", "cuSeconds": 6800.0}])])
    _res, st = _run(facts)
    blob = str(st["sent"])
    assert "bipin@newellco.com" in blob, "the card must name the heaviest user, not the first"
    assert "bipin@newellco.com 7400" not in blob, "a person must never carry the item's total"
    assert "aaron@newellco.com 7400" not in blob


def test_an_incident_with_no_attribution_names_nobody():
    """A fabricated accusation about a real person is worse than no answer at all."""
    _res, st = _run(_facts(peak=210.0, throttle=12.0, items=[_item(share=90.0, cu=None)]))
    assert "@newellco.com" not in str(st["sent"])
