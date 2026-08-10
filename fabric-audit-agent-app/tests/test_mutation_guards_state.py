"""Regression guards for state-machine invariants a mutation audit found UNTESTED.

Every guard pinned here was added as the fix for a real production incident, and every one of
them could be deleted outright with a fully green suite — the incident was recorded in a code
comment and nowhere else. The shape used throughout is the one that catches these: a multi-tick
replay through ``process_alerts`` against a Delta-FAITHFUL store (the real ``_to_row`` /
``_from_row``), because a memory store keeps whatever the state machine writes and hides both
field-persistence and high-water-mark bugs.
"""
import json
from datetime import datetime, timedelta, timezone

from fabric_audit_agent.automation.tier2_check import process_alerts
from fabric_audit_agent.automation.sweep_delivery import deliver_new_findings
from fabric_audit_agent.automation.materiality import load_cfg, is_escalation
from fabric_audit_agent.context_alerts import _from_row, _to_row
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


def _throttle(minutes, peak):
    return {"check": "throttle", "throttleMinutes": minutes, "peakCuPct": peak}


def _pressure(peak):
    return {"check": "pressure", "peakCuPct": peak}


def _overage(burndown, peak=105.0):
    return {"check": "overage", "overageTotalMs": 9000.0, "minutesToBurndown": burndown,
            "overageCumulativePct": 12.0, "peakCuPct": peak}


# --- 1. audit_alerts is a SHARED table -------------------------------------------------------

def test_tier2_sweep_does_not_deactivate_another_jobs_findings():
    """``audit_alerts`` is shared between the hourly sweep and the 5-minute tier2 job, and the
    sweep's rows are never in tier2's ``seen`` set. Without the ownership filter the resolution
    loop marked EVERY sweep finding currentlyActive=False within 5 minutes of creation: the
    notification center's Open tab keys off isFiringNow, so an hourly finding went invisible
    almost immediately and the daily digest filed it as stale backlog. The same write also
    overwrote the ticket's ``detail`` with "sweep finding (Warning)", destroying the sweep's
    recommendation text.

    No other test drives ``deliver_new_findings`` and ``process_alerts`` against ONE store, which
    is the only place the collision is observable.
    """
    store = create_delta_faithful_store()
    posts, sink = _sink()
    tickets = []
    finding = {"key": "model.bidirectional", "score": {"level": "Warning"},
               "what": "Sales model has bidirectional relationships",
               "where": "Fin/SalesModel", "recommendation": "Make the relationship single-direction"}

    deliver_new_findings([finding], alerts_store=store, delivery_sinks={"webhook": sink},
                         app_url="https://app", chat_writer=lambda m, t: "sweep-chat",
                         ticket_writer=lambda cid, meta: tickets.append(meta),
                         now_iso="2026-08-10T11:00:00Z")
    before = dict(store["_data"]["model.bidirectional"])
    assert before["currentlyActive"] is True and before["checkType"] == "model"

    # An unrelated tier2 capacity incident on the very next 5-minute sweep.
    process_alerts([_throttle(6.0, 210.0)], now_dt=T0,
                   **_kw(store, sink, ticket_writer=lambda cid, meta: tickets.append(meta)))

    after = store["_data"]["model.bidirectional"]
    assert after["currentlyActive"] is True, (
        "the tier2 sweep flipped a HOURLY-SWEEP finding to currentlyActive=False — it is not "
        "tier2's row to reason about; the notification center will hide it from the Open tab")
    assert after["status"] == "active"
    assert after["materialityReason"] == before["materialityReason"]
    assert after["investigationSummary"] == before["investigationSummary"]
    assert after["firstAlertedAt"] == before["firstAlertedAt"]
    assert after["runAt"] == before["runAt"], "tier2 rewrote another job's row"
    # And no ticket metadata was re-written for the sweep row (that is what clobbered `detail`).
    assert [m for m in tickets if m["incidentKey"] == "model.bidirectional"] == [tickets[0]]


# --- 2. high-water marks ---------------------------------------------------------------------

def test_partial_improvement_never_walks_the_incident_state_backwards():
    """An incident's identity is "the worst this has been", not "the most recent reading". When a
    capacity event partially improves, the row used to be rewritten downward (warn -> info,
    peak 130 -> 99, signal set narrowed), which both mislabelled the ticket in the notification
    center and made LATER spurious escalations easier — every comparison in ``is_escalation`` is
    against the stored value, so a lowered high-water mark re-opens the door for a re-fire at a
    level this incident has already been through.

    Kills three independently-deletable guards at once: the ``max(cur_m, pri_m)`` metric mark,
    the severity mark, and the signal-set union.

    Tick 2 reaches the escalation branch (the only branch that carries ``prior`` into
    ``_capacity_state``) purely via the burndown-collapse axis, so the reading is unambiguously
    BETTER on every axis the marks protect: severity warn -> info, peak 130 -> 99, set
    {overage, pressure} -> {overage}.
    """
    store = create_delta_faithful_store()
    posts, sink = _sink()
    kw = _kw(store, sink)

    process_alerts([_pressure(130.0), _overage(200.0, 130.0)], now_dt=T0, **kw)
    row = store["_data"]["capacity::capacity"]
    assert row["severity"] == "warn" and row["metric"] == 130.0

    acts = process_alerts([_overage(90.0, 99.0)], now_dt=T0 + timedelta(minutes=5), **kw)
    assert acts["escalation"] == ["capacity::capacity"], (
        "test setup: tick 2 must reach the escalation branch (burndown 200 -> 90 halving), "
        "otherwise the high-water marks are never exercised")

    row = store["_data"]["capacity::capacity"]
    assert row["severity"] == "warn", (
        "severity fell back to info on a partial improvement — the notification center will "
        "label a warn-grade capacity incident as informational")
    assert row["metric"] == 130.0, (
        "the metric high-water mark was overwritten with the improved reading (99); a later "
        "return to ~120 would then read as a fresh +20 escalation and card again")
    assert sorted(row["signalTypes"]) == ["overage", "pressure"], (
        "the stored signal set narrowed instead of holding the union; pressure re-appearing "
        "next tick would look like 'a new signal joined' and card again")


# --- 3. a weaker signal joining is not a worsening -------------------------------------------

def test_a_weaker_signal_joining_an_incident_is_not_an_escalation():
    """{pressure} -> {pressure, throttle_imminent} is "CU already over 100%" joined by "80% of a
    Fabric threshold" — nothing breached, nothing worsened. Those two signals derive from the SAME
    capacity dict and land in different sweep windows, so the join happens constantly; without the
    rank comparison it double-carded most real incidents (a strict superset alone was enough).
    """
    prior = {"severity": "warn", "metric": 110.0, "signalTypes": ["pressure"]}
    joined = {"check": "capacity_incident", "signalTypes": ["pressure", "throttle_imminent"],
              "peakCuPct": 110.0,
              # flat metrics on every other axis, so the signal-set axis is the only one in play
              "signals": [{"check": "pressure", "peakCuPct": 110.0},
                          {"check": "throttle_imminent", "worstPct": 80.0}]}
    assert is_escalation(joined, prior) is False, (
        "a weaker signal joining produced a second Teams card for an incident that had not "
        "worsened")

    # The counterpart must still break through, or the rank check has become a gag order.
    stronger = dict(joined, signalTypes=["pressure", "throttle"], throttleMinutes=None,
                    signals=[{"check": "pressure", "peakCuPct": 110.0},
                             {"check": "throttle", "throttleMinutes": 4.0, "peakCuPct": 110.0}])
    assert is_escalation(stronger, prior) is True


# --- 4. the composite materiality floor ------------------------------------------------------

def test_two_suppressible_signals_coalesced_do_not_manufacture_a_card():
    """Coalescing exists to turn N cards for one event into 1 — not to turn 0 into 1. A single
    30-second window at 100.4% CU fires ``pressure`` (suppressed: momentary, under the 105 bar)
    and ``throttle`` (0.5 min, under the 2.5 bar); merging two individually sub-threshold signals
    into an unconditional ``report`` invented a hard "Capacity incident (throttling + CU
    pressure)" alert out of nothing.

    The reasoner here deliberately has no ``judged: True`` key: as of the middle-tier fix an
    ambiguous decision only becomes a card when the reasoner declares itself capable of the
    judgement, so a card appearing at all means the composite classified as ``report``.
    """
    store = create_delta_faithful_store()
    posts, sink = _sink()
    acts = process_alerts(
        [{"check": "throttle", "throttleMinutes": 0.5, "peakCuPct": 100.4},
         {"check": "pressure", "peakCuPct": 100.4}],
        now_dt=T0, alerts_store=store, delivery_sinks={"webhook": sink}, reasoner=None,
        chat_writer=lambda m, t: "c1", app_url="https://app")
    assert posts == [], (
        "a composite of two SUPPRESSED sub-threshold signals paged someone; the composite "
        "materiality floor is gone")
    assert acts["new"] == []
    # Recorded, not lost: it still reaches the notification center and the daily digest.
    assert acts["informational"] == ["capacity::capacity"]
    assert store["_data"]["capacity::capacity"]["status"] == "informational"


# --- 5. a reopen is a FRESH occurrence -------------------------------------------------------

def test_reopen_restamps_identity_so_the_incident_can_escalate_again():
    """A recurrence after a human Resolve is a NEW occurrence of the ticket. Carrying the previous
    occurrence's high-water marks across the reopen labelled the ticket with a peak that was not
    happening (250% on a 130% event) and left it permanently unable to escalate — every later
    reading was compared against a peak from an event that had already been closed.

    130 / 155 rather than 110 / 135: the reopen branch runs BEFORE the materiality gate so an
    ambiguous value does reopen, but the follow-up tick must be judged on the metric axis alone,
    and a 110 reopen stores severity=info, which would let the 135 tick escalate on the
    severity-rank rule even with the re-stamp deleted. Report-tier values keep the assertion
    pointed at the thing under test.
    """
    store = create_delta_faithful_store()
    posts, sink = _sink()
    resolved_chat = {"id": None}
    ack = {"get": lambda c: ({"status": "resolved", "resolutionNote": "scaled the capacity"}
                             if c and c == resolved_chat["id"] else None),
           "reopen": lambda c: None}
    kw = _kw(store, sink)

    process_alerts([_pressure(250.0)], now_dt=T0, **kw)          # the original 250% event
    resolved_chat["id"] = store["_data"]["capacity::capacity"]["chatId"]
    assert store["_data"]["capacity::capacity"]["metric"] == 250.0

    # goes quiet (inside the grace window, so the row stays active) then a human marks it resolved
    process_alerts([], now_dt=T0 + timedelta(minutes=5), **kw)
    assert store["_data"]["capacity::capacity"]["currentlyActive"] is False

    reopen_at = T0 + timedelta(minutes=10)
    acts = process_alerts([_pressure(130.0)], now_dt=reopen_at, ack_store=ack, **kw)
    assert acts["reopened"] == ["capacity::capacity"]
    row = store["_data"]["capacity::capacity"]
    assert row["firstAlertedAt"] == reopen_at.isoformat().replace("+00:00", "Z"), (
        "firstAlertedAt still points at the PREVIOUS occurrence — the card's 'When / first "
        "noticed' and the ticket's firstDetected both claim an incident that was already closed")
    assert row["metric"] == 130.0, (
        "the reopened ticket carried the resolved occurrence's 250% high-water mark")

    acts = process_alerts([_pressure(155.0)], now_dt=T0 + timedelta(minutes=15), ack_store=ack,
                          **kw)
    assert acts["escalation"] == ["capacity::capacity"], (
        "the reopened incident cannot escalate: +25 points over its own opening reading was "
        "measured against a stale 250% high-water from the resolved occurrence")


# --- 6. hostile signal_types on the way IN ---------------------------------------------------

def test_from_row_coerces_a_non_list_signal_types_to_empty():
    """``signal_types`` is a Delta STRING column, so anything can be in it: a hand-run backfill, a
    raw MERGE, a future writer. A JSON string decodes to ``"throttle"``, and ``set("throttle")``
    iterates CHARACTERS — every signal then looks new and a card fires every 5 minutes. A
    non-iterable is worse: it raises inside ``is_escalation``, escapes ``process_alerts``, and is
    swallowed by ``_deliver``'s bare except, silencing EVERY alert for that sweep.
    """
    assert _from_row({"signal_types": '"throttle"'})["signalTypes"] == []
    assert _from_row({"signal_types": "42"})["signalTypes"] == []
    assert _from_row({"signal_types": "not json at all"})["signalTypes"] == []
    # a well-formed value must still survive the round trip untouched
    assert _from_row(_to_row({"signalTypes": ["pressure", "throttle"]}))["signalTypes"] == [
        "pressure", "throttle"]


def test_a_hostile_prior_row_neither_cards_nor_silences_the_sweep():
    """Defence in depth for the decode above: with a corrupt ``signal_types`` already in the
    table, an unchanged incident must stay silent (not card every tick) and the sweep must not
    raise.

    The no-card half of that holds even without the ``_from_row`` coercion, because
    ``materiality`` type-guards the same value independently. What only the coercion prevents is
    the corruption PERSISTING: the row is re-serialised on the next write, so an uncoerced
    ``"throttle"`` stays in the table forever, one guard away from the card-every-tick bug on any
    future code path that trusts the field."""
    hostile = _to_row({"incidentKey": "capacity::capacity", "status": "active",
                       "severity": "warn", "checkType": "capacity_incident",
                       "resource": "capacity", "chatId": "c1", "metric": 210.0,
                       "firstAlertedAt": "2026-08-10T11:00:00Z",
                       "lastAlertedAt": "2026-08-10T11:00:00Z", "escalationCount": 0,
                       "currentlyActive": True, "throttleMinutes": 8.5})
    hostile["signal_types"] = '"throttle"'
    data = {"capacity::capacity": _from_row(hostile)}
    store = {"query_active": lambda: {k: dict(v) for k, v in data.items()},
             "query_pending": lambda: {}, "query_informational": lambda: {},
             "upsert": lambda a: data.__setitem__(a["incidentKey"], _from_row(_to_row(a))),
             "resolve": lambda k, at: None, "delete": lambda k: data.pop(k, None)}
    posts, sink = _sink()

    acts = process_alerts([_throttle(8.5, 210.0), _pressure(210.0)], now_dt=T0,
                          **_kw(store, sink))
    assert posts == [], "a corrupt stored signal set re-carded an unchanged incident"
    assert acts["silent"] == ["capacity::capacity"]
    assert isinstance(json.loads(_to_row(data["capacity::capacity"])["signal_types"] or "[]"), list)
