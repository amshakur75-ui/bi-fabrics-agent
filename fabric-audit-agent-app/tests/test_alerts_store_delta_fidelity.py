"""Delta-FIDELITY regression guard for the Tier-2 alert state machine.

WHY THIS FILE EXISTS
--------------------
``create_alerts_store_memory`` stores the whole alert dict, so ANY field written onto a row
"persists" in tests even when production would discard it. The production Delta store builds
its row by iterating ``context_alerts._FIELDS`` — a key that isn't listed there is silently
dropped on write and reads back as ``None`` on the next tick.

On 2026-08-10 that gap shipped three unmapped fields (``absenceCount``, ``signalTypes``,
``throttleMinutes``) with a fully green 2106-test suite. The real-world effect, measured by
replaying the actual Aug-5 capacity events:

    in-memory store  -> 2 Teams cards, incident auto-resolves   (what the tests asserted)
    Delta round-trip -> 8 Teams cards, incident NEVER resolves  (what production would do)

i.e. the entire dedup feature was inert in production and every re-fire counted as an
escalation. These tests run the state machine through a store that applies the REAL
``_to_row``/``_from_row``, so the next unmapped field fails here instead of in prod.
"""
from datetime import datetime, timedelta, timezone

from fabric_audit_agent.automation.tier2_check import process_alerts
from fabric_audit_agent.automation.materiality import load_cfg
from fabric_audit_agent.context_alerts import _to_row, _from_row, _FIELDS

T0 = datetime(2026, 8, 5, 13, 50, tzinfo=timezone.utc)


def create_delta_faithful_store():
    """A memory store that persists EXACTLY what the Delta store would.

    Every upsert is pushed through ``_to_row`` and pulled back through ``_from_row``, so an
    unmapped field vanishes here the same way it vanishes in Unity Catalog.
    """
    data = {}

    def upsert(alert):
        data[alert["incidentKey"]] = _from_row(_to_row(alert))

    def query_active():
        return {k: dict(v) for k, v in data.items() if v.get("status") == "active"}

    def resolve(key, at):
        cur = data.get(key)
        if cur is not None and cur.get("status") == "active":
            cur["status"] = "resolved"
            cur["resolvedAt"] = at

    return {"query_active": query_active, "query_pending": lambda: {},
            "query_informational": lambda: {}, "upsert": upsert, "resolve": resolve,
            "delete": lambda k: data.pop(k, None), "_data": data}


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


def test_every_row_field_the_state_machine_writes_is_mapped_in_FIELDS():
    """Structural guard: run one full alert cycle against a recording store and assert that
    every key the state machine puts on a row has a home in ``_FIELDS``. This is the cheap
    check that catches the next unmapped field at authoring time."""
    mapped = {cc for cc, _ in _FIELDS}
    seen = {}

    def recording_upsert(alert):
        seen.update(alert)

    store = {"query_active": lambda: {}, "query_pending": lambda: {},
             "query_informational": lambda: {}, "upsert": recording_upsert,
             "resolve": lambda k, at: None, "delete": lambda k: None}
    posts, sink = _sink()
    trigs = [{"check": "throttle", "throttleMinutes": 8.5, "peakCuPct": 210.0},
             {"check": "pressure", "peakCuPct": 210.0}]
    process_alerts(trigs, now_dt=T0, **_kw(store, sink))

    unmapped = sorted(set(seen) - mapped)
    assert unmapped == [], (
        f"process_alerts writes {unmapped} onto the alert row, but they are not in "
        "context_alerts._FIELDS — they will be SILENTLY DROPPED by the production Delta "
        "store and read back as None on the next tick. Add them to _FIELDS (plus _schema() "
        "and _COL_SQL_TYPE) or stop writing them.")


def test_absence_count_survives_and_incident_actually_resolves():
    """Quiet-to-resolve depends on absenceCount accumulating ACROSS ticks. If it doesn't
    persist, `int(None or 0) + 1` pins it at 1 forever, the `>= quiet_ticks` branch is dead
    code, and the ticket never closes."""
    store = create_delta_faithful_store()
    posts, sink = _sink()
    kw = _kw(store, sink, cfg={"quiet_ticks": 3})
    trig = {"check": "throttle", "throttleMinutes": 5.0, "peakCuPct": 105.0}

    process_alerts([trig], now_dt=T0, **kw)
    process_alerts([], now_dt=T0 + timedelta(minutes=5), **kw)     # absence 1
    assert store["_data"]["capacity::capacity"]["absenceCount"] == 1
    process_alerts([], now_dt=T0 + timedelta(minutes=10), **kw)    # absence 2
    assert store["_data"]["capacity::capacity"]["absenceCount"] == 2
    a = process_alerts([], now_dt=T0 + timedelta(minutes=15), **kw)  # absence 3 -> resolve
    assert a["resolved"] == ["capacity::capacity"]
    assert store["_data"]["capacity::capacity"]["status"] == "resolved"


def test_signal_types_survive_so_a_steady_incident_stays_silent():
    """The headline regression. A capacity event that persists unchanged across many sweeps
    must produce exactly ONE card. If signalTypes doesn't persist, `cur_sigs - pri_sigs` is
    non-empty every tick and every tick escalates -> a card every 5 minutes."""
    store = create_delta_faithful_store()
    posts, sink = _sink()
    kw = _kw(store, sink)
    trigs = [{"check": "throttle", "throttleMinutes": 8.5, "peakCuPct": 210.0},
             {"check": "pressure", "peakCuPct": 210.0}]

    for i in range(12):    # one solid hour of the SAME unchanged incident
        process_alerts(list(trigs), now_dt=T0 + timedelta(minutes=5 * i), **kw)

    assert len(posts) == 1, (
        f"a steady capacity incident produced {len(posts)} Teams cards over 12 sweeps; "
        "it must produce exactly 1 (the initial alert). More than 1 means the escalation "
        "state is not persisting.")
    row = store["_data"]["capacity::capacity"]
    assert sorted(row["signalTypes"]) == ["pressure", "throttle"]
    assert row["escalationCount"] == 0


def test_signal_types_round_trip_as_a_list_not_a_json_string():
    """``signalTypes`` is a list in the alert dict and a JSON string in the Delta row.
    ``_from_row`` must decode it back to a list — if it leaked back as a raw string,
    `set(prior["signalTypes"])` would iterate CHARACTERS and every comparison would be wrong."""
    store = create_delta_faithful_store()
    posts, sink = _sink()
    trigs = [{"check": "throttle", "throttleMinutes": 8.5, "peakCuPct": 210.0},
             {"check": "pressure", "peakCuPct": 210.0}]
    process_alerts(trigs, now_dt=T0, **_kw(store, sink))
    stored = store["_data"]["capacity::capacity"]["signalTypes"]
    assert isinstance(stored, list) and set(stored) == {"throttle", "pressure"}


def test_a_genuine_worsening_still_breaks_through_after_round_trip():
    """Dedup must not become a gag order: when the incident actually gets worse, the card
    still fires. Guards against over-correcting the fix above."""
    store = create_delta_faithful_store()
    posts, sink = _sink()
    kw = _kw(store, sink)

    process_alerts([{"check": "pressure", "peakCuPct": 110.0}], now_dt=T0, **kw)
    assert len(posts) == 1
    # same signal, unchanged -> silent
    process_alerts([{"check": "pressure", "peakCuPct": 110.0}],
                   now_dt=T0 + timedelta(minutes=5), **kw)
    assert len(posts) == 1
    # throttling STARTS -> genuine worsening -> second card
    process_alerts([{"check": "pressure", "peakCuPct": 112.0},
                    {"check": "throttle", "throttleMinutes": 6.0, "peakCuPct": 112.0}],
                   now_dt=T0 + timedelta(minutes=10), **kw)
    assert len(posts) == 2


def test_aug5_replay_matches_between_memory_and_delta_semantics():
    """End-to-end replay of the REAL 2026-08-05 capacity events (8 throttle events across the
    afternoon, pulled from the live alerts table). Pinned at 2 cards: the initial incident
    plus one genuine escalation when peak climbed 102% -> 121% and throttle 1.5 -> 4.5 min.

    Before the _FIELDS fix this replay produced 8 cards under Delta semantics — one per
    event, i.e. identical to having no dedup at all."""
    aug5 = [("13:50", 102.0, 1.5), ("14:45", 121.0, 4.5), ("15:30", 117.0, 1.0),
            ("15:45", 133.0, 2.5), ("16:15", 127.0, 1.5), ("16:55", 118.0, 3.0),
            ("17:25", 129.0, 2.5), ("18:25", 102.0, 0.5)]
    base = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
    events = {}
    for hhmm, peak, thr in aug5:
        hh, mm = (int(x) for x in hhmm.split(":"))
        events[base + timedelta(hours=hh, minutes=mm)] = (peak, thr)

    store = create_delta_faithful_store()
    posts, sink = _sink()
    kw = _kw(store, sink)

    t = base + timedelta(hours=13, minutes=50)
    end = base + timedelta(hours=19, minutes=30)
    while t <= end:
        peak, thr = events.get(t, (None, None))
        trigs = []
        if peak is not None:
            trigs.append({"check": "throttle", "throttleMinutes": thr, "peakCuPct": peak})
            trigs.append({"check": "pressure", "peakCuPct": peak})
        process_alerts(trigs, now_dt=t, **kw)
        t += timedelta(minutes=5)

    assert len(posts) == 2, f"Aug-5 replay produced {len(posts)} cards, expected 2"
    assert store["_data"]["capacity::capacity"]["status"] == "resolved"
