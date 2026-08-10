"""Guards for five incident-response rules that a mutation audit could delete unnoticed.

Each rule below was added to fix a specific production failure, the code comment records the
incident — and yet flipping the rule back left the whole suite green. That is the gap these
tests close: BOUNDARIES (a ``>=`` quietly becoming ``>``) and MULTI-TICK state transitions (a
streak or a first-detected timestamp that only misbehaves on the third or fourth sweep) are
invisible to single-tick, single-value tests.

Covered here:
  1. concentration severity boundary — exactly 50% share is warn (every other ``severity_of``
     branch already has a boundary test; this one did not).
  2. correlation half-window boundary — a user spike exactly ``window_min`` from the capacity
     anchor still correlates.
  3. the true-CU gate that decides whether a RECURRING attribution pattern earns a live ticket.
  4. informational rows must be visible to the hysteresis block, so a days-old pattern keeps its
     real first-detected time.
  5. dropping below the materiality bar must break a hysteresis streak.
"""
from datetime import datetime, timedelta, timezone

from fabric_audit_agent.automation.correlation import correlate_user_spikes_with_capacity
from fabric_audit_agent.automation.incident import severity_of
from fabric_audit_agent.automation.materiality import load_cfg
from fabric_audit_agent.automation.tier2_check import process_alerts
from fabric_audit_agent.context_alerts import _from_row, _to_row

T0 = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _delta_faithful_store():
    """Delta-faithful store that also answers ``query_pending`` / ``query_informational``.

    ``tests.test_alerts_store_delta_fidelity.create_delta_faithful_store`` stubs those two out
    (``lambda: {}``), which is fine for the capacity-incident lifecycle but makes hysteresis and
    the informational tier permanently stateless — exactly the state the tests below drive across
    ticks. Rows still round-trip through the REAL ``_to_row``/``_from_row``, so a field the state
    machine writes but ``_FIELDS`` does not map vanishes here like it does in Unity Catalog (that
    divergence was a P0: 2 cards in memory, 8 in production).
    """
    data = {}

    def _by_status(status):
        return {k: dict(v) for k, v in data.items() if v.get("status") == status}

    def upsert(alert):
        data[alert["incidentKey"]] = _from_row(_to_row(alert))

    def resolve(key, at):
        cur = data.get(key)
        if cur is not None and cur.get("status") == "active":
            cur["status"] = "resolved"
            cur["resolvedAt"] = at

    return {"query_active": lambda: _by_status("active"),
            "query_pending": lambda: _by_status("pending"),
            "query_informational": lambda: _by_status("informational"),
            "upsert": upsert, "resolve": resolve,
            "delete": lambda k: data.pop(k, None), "_data": data}


def _sink():
    posts = []
    return posts, {"deliver": lambda b: (posts.append(b), {"delivered": True, "status": 202})[1]}


def _concentration(share, *, recurring=False):
    trig = {"check": "concentration", "item": "Sales Report", "workspace": "Finance",
            "sharePct": share, "owner": "owner@x", "topUsers": [{"user": "u1"}]}
    if recurring:
        trig["recurrence"] = {"isRecurring": True,
                              "matchingFindings": ["capacity.concentration::Finance/Sales Report"]}
    return trig


# --- 1. concentration severity boundary --------------------------------------------------

def test_a_share_of_exactly_fifty_percent_is_warn_severity():
    """The bar is "half of the capacity's monitored activity on one item", so 50.0 is ON the wrong
    side of it, not under. Severity is not cosmetic: it is the stored row's severity, the first
    rule ``is_escalation`` evaluates (a severity-RANK comparison), and the label the notification
    center shows — so a boundary slip both mis-labels the ticket and silently changes which later
    worsenings can break through."""
    assert severity_of(_concentration(50.0)) == "warn"
    assert severity_of(_concentration(50.1)) == "warn"
    assert severity_of(_concentration(49.9)) == "info"


# --- 2. correlation half-window boundary -------------------------------------------------

def _spike(when, *, user="u1", cu=900.0, p95=100.0):
    return {"resource": user, "when": when,
            "evidence": {"cuSeconds": cu, "baselineP95": p95, "baselineSource": "user",
                         "item": "Sales Report", "operation": "Query"}}


def test_a_user_spike_exactly_at_the_window_edge_still_correlates():
    """The half-window is INCLUSIVE. Both feeds are 5-minute-bucketed and the sweep cadence is
    5 minutes, so "exactly 5 minutes apart" is the single most likely spacing between a capacity
    peak and the user query that caused it — an exclusive comparison drops precisely the pairing
    the correlator exists to make, and the one Teams card loses the name of the driver."""
    anchor = "2026-08-05T14:45:00Z"
    trig = {"check": "pressure", "peakCuPct": 210.0, "peakAt": anchor}
    at_edge = [_spike("2026-08-05T14:50:00Z"), _spike("2026-08-05T14:40:00Z", user="u2")]
    out = correlate_user_spikes_with_capacity(at_edge, [trig], window_min=5)
    assert [s["user"] for s in out[0]["correlatedUserSpikes"]] == ["u1", "u2"]
    assert out[0]["correlatedUserSpikeCount"] == 2

    # ...and the window is still a window: one second past the edge is out.
    out2 = correlate_user_spikes_with_capacity([_spike("2026-08-05T14:50:01Z")], [trig],
                                              window_min=5)
    assert "correlatedUserSpikes" not in out2[0]


# --- 3. only a TRUE-CU event promotes a recurring attribution pattern --------------------

def _kw(store, sink, **over):
    cfg = load_cfg()
    cfg.update(over.pop("cfg", {}))
    return dict(alerts_store=store, delivery_sinks={"webhook": sink},
                chat_writer=lambda m, t: "c1", app_url="https://app", cfg=cfg, **over)


def test_an_early_warning_capacity_signal_does_not_promote_a_recurring_pattern():
    """``extreme_peak`` (a big spike Fabric's smoothing absorbed) and ``throttle_imminent`` are
    EARLY WARNINGS: nothing has breached true CU. Counting them as "capacity-linked" is the exact
    mistake the gate was written to stop — it let a known-stable, weeks-old concentration pattern
    mint a live "go investigate this person" ticket plus an LLM investigation, which then
    concluded the named user wasn't the driver. Absent a real over-threshold event the pattern is
    recorded informational and rides the daily digest instead."""
    for early_warning in ({"check": "extreme_peak", "peakCuPct": 260.0, "extremeThreshold": 200.0},
                          {"check": "throttle_imminent", "worstPct": 85.0}):
        store = _delta_faithful_store()
        posts, sink = _sink()
        asked = []

        def reasoner(t):
            asked.append(t.get("check"))
            return {"markdown": "m", "summary": "s", "report": True, "judged": True}

        acts = process_alerts([_concentration(45.0, recurring=True), early_warning],
                             now_dt=T0, reasoner=reasoner,
                             **_kw(store, sink, cfg={"hysteresis_ticks": 1}))
        key = "concentration::Finance/Sales Report"
        assert acts["informational"] == [key], f"{early_warning['check']} promoted the pattern"
        assert key not in acts["new"]
        assert "concentration" not in asked, "an LLM investigation was spent on a stable pattern"
        assert store["_data"][key]["status"] == "informational"


def test_a_real_true_cu_event_does_promote_a_recurring_pattern():
    """The counterpart — the gate must not become a gag order. When the recurring pattern
    coincides with an actual over-threshold capacity event, it IS the live incident's likely
    driver and earns the ticket."""
    store = _delta_faithful_store()
    posts, sink = _sink()
    acts = process_alerts([_concentration(45.0, recurring=True),
                           {"check": "pressure", "peakCuPct": 130.0}],
                          now_dt=T0,
                          reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True},
                          **_kw(store, sink, cfg={"hysteresis_ticks": 1}))
    assert acts["new"] == ["concentration::Finance/Sales Report", "capacity::capacity"]
    assert acts["informational"] == []


# --- 4. informational rows must be visible to the hysteresis block -----------------------

def test_an_informational_pattern_keeps_its_real_first_detected_time_across_ticks():
    """Informational rows are NOT in ``query_pending`` (different status). Read only the pending
    set and the hysteresis block treats a long-standing informational row as a brand-new
    candidate, rewriting ``firstAlertedAt`` to "now" every time the row cycles back through
    pending — so the daily digest reported a pattern that had run for days as first detected 15
    minutes ago, and no one could see it was chronic.

    Multi-tick on purpose: the reset only happens on the tick AFTER the row goes informational,
    which is the fourth sweep with the default 3-tick hysteresis.
    """
    store = _delta_faithful_store()
    posts, sink = _sink()
    kw = _kw(store, sink, reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True})
    key = "concentration::Finance/Sales Report"

    first_seen = None
    informational_ticks = 0
    for i in range(10):        # ~50 minutes of the same stable, recurring pattern
        process_alerts([_concentration(45.0, recurring=True)],
                       now_dt=T0 + timedelta(minutes=5 * i), **kw)
        row = store["_data"][key]
        if first_seen is None:
            first_seen = row["firstAlertedAt"]
        if row["status"] == "informational":
            informational_ticks += 1
        assert row["firstAlertedAt"] == first_seen, (
            f"tick {i} ({row['status']}) reset firstAlertedAt to {row['firstAlertedAt']}; the "
            f"pattern has been detected since {first_seen}")
    assert informational_ticks >= 2, "the pattern never reached the informational tier"
    assert posts == [], "a stable informational pattern paged the Teams channel"


# --- 5. dropping below the bar breaks the hysteresis streak ------------------------------

def test_a_candidate_that_drops_below_the_bar_loses_its_streak():
    """Hysteresis is anti-FLAP: it promotes only on N CONSECUTIVE presences. A candidate whose
    share dips under the suppress bar is a flap, so its streak row must be deleted — the shared
    end-of-run cleanup cannot do it, because a suppressed key is in ``seen`` and is therefore
    skipped there. Keep the row and the counter survives every dip, so a wobbling pattern
    accumulates NON-consecutive presence and eventually pages anyway, which is the precise
    behaviour hysteresis exists to prevent.
    """
    store = _delta_faithful_store()
    posts, sink = _sink()
    kw = _kw(store, sink, reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True},
             cfg={"hysteresis_ticks": 3})
    key = "concentration::Finance/Sales Report"

    def tick(i, share):
        return process_alerts([_concentration(share)], now_dt=T0 + timedelta(minutes=5 * i), **kw)

    tick(0, 35.0)                                   # streak 1
    assert store["_data"][key]["presenceCount"] == 1
    tick(1, 35.0)                                   # streak 2 — one short of promoting
    assert store["_data"][key]["presenceCount"] == 2
    acts = tick(2, 31.0)                            # under the suppress bar -> flap
    assert acts["silent"] == [key]
    assert key not in store["_data"], "the broken streak's row survived the dip"

    acts = tick(3, 35.0)                            # back, but this is presence 1 of 3 again
    assert acts["pending"] == [key], "a post-dip re-appearance promoted on accumulated presence"
    assert acts["informational"] == [] and acts["new"] == []
    assert store["_data"][key]["presenceCount"] == 1
    assert tick(4, 35.0)["pending"] == [key]
    assert tick(5, 35.0)["pending"] == [], "three consecutive presences must promote"
    assert posts == []


def test_an_established_informational_pattern_settles_instead_of_cycling():
    """The other half of the bug above, found while writing it.

    The informational upsert never wrote ``presenceCount``, so on the tick AFTER a row went
    informational the hysteresis block computed ``count = 1`` and rewrote it back to
    status='pending'. The row therefore cycled pending/pending/informational forever rather than
    settling: an established, stable, already-classified pattern was re-promoted through the
    persistence gate every three sweeps. Merging the informational rows into ``pending`` kept
    firstAlertedAt honest through that cycle (the test above), but the cycling itself is a separate
    defect — the digest's "stable patterns" section flickered, showing a chronic pattern on roughly
    one tick in three.
    """
    store = _delta_faithful_store()
    posts, sink = _sink()
    kw = _kw(store, sink, reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True})
    key = "concentration::Finance/Sales Report"

    statuses = []
    for i in range(10):
        process_alerts([_concentration(45.0, recurring=True)],
                       now_dt=T0 + timedelta(minutes=5 * i), **kw)
        statuses.append(store["_data"][key]["status"])

    # Once it has reached informational it must STAY there while the pattern keeps presenting.
    first_info = statuses.index("informational")
    assert set(statuses[first_info:]) == {"informational"}, (
        f"the row cycled instead of settling: {statuses}")
    assert posts == []
