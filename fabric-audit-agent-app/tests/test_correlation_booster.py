"""B3 (Design A' Phase B) — correlation booster: a per-user baseline spike overlapping with an
active capacity incident is stronger signal than either alone. The correlator annotates
capacity triggers with the correlated user spikes so the ONE composite Teams card names the
likely driver on the same card.

Covers the pure correlation function + its wiring into tier2 (baseline store threaded through,
composite hoists spikes from any component, card renders top 3, store failure is isolated).
"""
from datetime import datetime, timedelta, timezone

from fabric_audit_agent.automation.correlation import correlate_user_spikes_with_capacity
from fabric_audit_agent.automation.tier2_check import (
    _coalesce_capacity_family, _facts_for, _title_for, run_tier2_check,
)
from fabric_audit_agent.context_user_baseline import create_user_baseline_store_memory
from fabric_audit_agent.context_alerts import create_alerts_store_memory


T0 = datetime(2026, 8, 5, 13, 52, 0, tzinfo=timezone.utc)


def _spike(user, when, cu=100.0, p95=10.0, item="Sales", op="ExecuteQuery",
           source="personalized"):
    return {"type": "activity.user-baseline-deviation", "resource": user,
            "when": when, "evidence": {"user": user, "cuSeconds": cu,
                                       "baselineP95": p95, "item": item,
                                       "operation": op, "baselineSource": source}}


def test_correlate_annotates_capacity_trigger_with_overlapping_spike():
    """The bread-and-butter case: a throttle at 13:52, a user spike at 13:53 with 8x their p95
    -> the throttle trigger gets `correlatedUserSpikes: [{user: ..., ratio: 8.0}]`."""
    spikes = [_spike("bipin@x", "2026-08-05T13:53:00Z", cu=8000.0, p95=1000.0)]
    trigs = [{"check": "throttle", "throttleMinutes": 5.0, "peakCuPct": 105.0,
              "peakAt": "2026-08-05T13:52:00Z"}]
    out = correlate_user_spikes_with_capacity(spikes, trigs, window_min=5)
    assert out[0]["correlatedUserSpikes"][0]["user"] == "bipin@x"
    assert out[0]["correlatedUserSpikes"][0]["ratio"] == 8.0


def test_correlate_skips_spikes_outside_window():
    """A user spike 30 minutes after the throttle is NOT correlated — different event."""
    spikes = [_spike("late@x", "2026-08-05T14:22:00Z", cu=500.0, p95=50.0)]
    trigs = [{"check": "throttle", "peakAt": "2026-08-05T13:52:00Z"}]
    out = correlate_user_spikes_with_capacity(spikes, trigs, window_min=5)
    assert "correlatedUserSpikes" not in out[0]


def test_correlate_falls_back_to_run_at_when_trigger_has_no_peakat():
    """Not every trigger carries peakAt (e.g. throttle_imminent from threshold pcts). The
    correlator uses the sweep's run_at as the anchor so those still correlate."""
    spikes = [_spike("bipin@x", "2026-08-05T13:53:00Z")]
    trigs = [{"check": "throttle_imminent", "worstPct": 88.0}]
    out = correlate_user_spikes_with_capacity(spikes, trigs, window_min=5,
                                                run_at="2026-08-05T13:52:00Z")
    assert out[0]["correlatedUserSpikes"][0]["user"] == "bipin@x"


def test_correlate_leaves_non_capacity_triggers_untouched():
    """Concentration and other non-capacity-family triggers pass through unchanged."""
    spikes = [_spike("bipin@x", "2026-08-05T13:52:00Z")]
    trigs = [{"check": "concentration", "item": "X", "workspace": "W", "sharePct": 45.0}]
    out = correlate_user_spikes_with_capacity(spikes, trigs, window_min=5)
    assert out[0] is trigs[0]                              # identity preserved


def test_correlate_sorts_worst_offender_first():
    """Multiple spikes at once — the composite card leads with the biggest, so a human eye
    lands on the likely driver first."""
    spikes = [
        _spike("a@x", "2026-08-05T13:52:00Z", cu=500.0, p95=50.0),
        _spike("b@x", "2026-08-05T13:53:00Z", cu=8000.0, p95=100.0),
        _spike("c@x", "2026-08-05T13:53:30Z", cu=1200.0, p95=200.0),
    ]
    trigs = [{"check": "throttle", "peakAt": "2026-08-05T13:52:00Z"}]
    out = correlate_user_spikes_with_capacity(spikes, trigs, window_min=5)
    users = [s["user"] for s in out[0]["correlatedUserSpikes"]]
    assert users == ["b@x", "c@x", "a@x"]                  # biggest cuSeconds first


def test_correlate_handles_zero_p95_gracefully():
    """A degenerate estate baseline (p95 = 0) shouldn't blow up the ratio calc — ratio just
    stays None, the spike still gets included."""
    spikes = [_spike("edge@x", "2026-08-05T13:53:00Z", cu=5.0, p95=0.0)]
    trigs = [{"check": "throttle", "peakAt": "2026-08-05T13:52:00Z"}]
    out = correlate_user_spikes_with_capacity(spikes, trigs, window_min=5)
    assert out[0]["correlatedUserSpikes"][0]["ratio"] is None


def test_correlate_ignores_spikes_missing_timestamps():
    """A spike with no ``when`` can't be correlated — silently skipped, doesn't crash."""
    spikes = [_spike("nots@x", None), _spike("ok@x", "2026-08-05T13:53:00Z")]
    trigs = [{"check": "throttle", "peakAt": "2026-08-05T13:52:00Z"}]
    out = correlate_user_spikes_with_capacity(spikes, trigs, window_min=5)
    users = [s["user"] for s in out[0]["correlatedUserSpikes"]]
    assert users == ["ok@x"]


def test_correlate_stable_on_empty_inputs():
    assert correlate_user_spikes_with_capacity([], []) == []
    trigs = [{"check": "throttle", "peakAt": "2026-08-05T13:52:00Z"}]
    assert correlate_user_spikes_with_capacity([], trigs) == trigs
    assert correlate_user_spikes_with_capacity([_spike("a@x", "2026-08-05T13:52Z")], []) == []


def test_coalesce_hoists_correlated_spikes_onto_composite():
    """When throttle + pressure both fire with correlated spikes attached, the composite
    picks up the merged spike list (deduped by user) so the composite card names the driver."""
    trigs = [
        {"check": "throttle", "throttleMinutes": 5.0, "peakCuPct": 210.0,
         "correlatedUserSpikes": [{"user": "bipin@x", "cuSeconds": 8000.0, "ratio": 8.0}]},
        {"check": "pressure", "peakCuPct": 210.0,
         "correlatedUserSpikes": [{"user": "bipin@x", "cuSeconds": 8000.0, "ratio": 8.0},
                                  {"user": "amy@x", "cuSeconds": 3000.0, "ratio": 3.0}]},
    ]
    out = _coalesce_capacity_family(trigs)
    assert len(out) == 1
    c = out[0]
    assert c["check"] == "capacity_incident"
    users = [s["user"] for s in c["correlatedUserSpikes"]]
    # Deduped: bipin appears once even though both components carried it
    assert users == ["bipin@x", "amy@x"]


def test_composite_card_lists_correlated_spikes_as_a_fact():
    """The single Teams card renders up to 3 correlated spikes as a compact fact — 'bipin@x
    (ExecuteQuery on Sales) — 8000.0 CPU-s, 8.0x baseline' etc."""
    c = {"check": "capacity_incident", "signalTypes": ["throttle", "pressure"],
         "peakCuPct": 210.0, "throttleMinutes": 5.0,
         "correlatedUserSpikes": [
             {"user": "bipin@x", "cuSeconds": 8000.0, "ratio": 8.0,
              "item": "Sales", "operation": "ExecuteQuery"},
             {"user": "amy@x", "cuSeconds": 3000.0, "ratio": 3.0,
              "item": "Finance", "operation": "Refresh"},
         ]}
    facts = dict(_facts_for(c))
    assert "Correlated user spikes" in facts
    line = facts["Correlated user spikes"]
    assert "bipin@x" in line and "amy@x" in line
    assert "8000.0 CPU-s" in line and "8.0x baseline" in line
    assert "ExecuteQuery on Sales" in line


def test_composite_card_truncates_correlated_spikes_to_top_3():
    """More than 3 spikes -> the card lists top 3 by cuSeconds and shows a '+N more in chat'
    marker so it stays readable. Full list still lives in the notification-center chat."""
    c = {"check": "capacity_incident", "signalTypes": ["throttle"],
         "correlatedUserSpikes": [
             {"user": f"user{i}@x", "cuSeconds": 1000 - i} for i in range(6)
         ]}
    line = dict(_facts_for(c))["Correlated user spikes"]
    assert "+3 more in chat" in line


def _sink():
    posts = []
    return posts, {"deliver": lambda b: (posts.append(b), {"delivered": True, "status": 202})[1]}


def test_run_tier2_check_wires_baseline_and_correlation_end_to_end():
    """The full B3 flow inside a single sweep: a throttle event + a same-window user spike ->
    ONE composite card whose facts include both signals AND the correlated spike."""
    from fabric_audit_agent.automation.materiality import load_cfg
    _cfg = load_cfg()
    _cfg["hysteresis_ticks"] = 1
    baseline_store = create_user_baseline_store_memory([
        {"scope": "user", "user": "bipin@x", "p50": 100.0, "p95": 1000.0, "count": 30,
         "min": 10.0, "max": 1200.0, "asOf": "2026-08-05T00:00:00Z"},
    ])
    events = [{"user": "bipin@x", "cuSeconds": 8000.0, "item": "Sales",
               "operation": "ExecuteQuery", "ts": "2026-08-05T13:53:00Z"}]
    facts = {
        "capacity": {"peakCuPct": 210.0, "throttleMinutes": 5.0, "peakAt": "2026-08-05T13:52:00Z",
                     "maxInteractiveDelayPct": 82.0},
        "items": [], "events": events,
    }
    collector = {"collect": lambda: facts}
    alerts_store = create_alerts_store_memory()
    posts, sink = _sink()

    res = run_tier2_check(
        collector, delivery_sinks={"webhook": sink}, alerts_store=alerts_store,
        reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True},
        chat_writer=lambda md, title: "c1", app_url="https://app",
        now_dt=T0, config={}, baseline_store=baseline_store,
    )
    assert res["triggered"] is True
    # ONE Teams card for the whole composite incident (throttle + pressure + throttle_imminent
    # + extreme_peak from the 210% peak all coalesce under capacity::capacity)
    assert len(posts) == 1
    card_body = posts[0]["attachments"][0]["content"]["body"]
    text = str(card_body)
    assert "bipin@x" in text                              # correlated spike surfaced on the card


def test_correlation_booster_isolated_when_baseline_store_raises():
    """A misbehaving store must NOT crash the sweep — correlation is best-effort. Triggers
    still fire, just without correlated spike annotations."""
    bad_store = {"get_user": lambda u: (_ for _ in ()).throw(RuntimeError("boom")),
                 "get_estate": lambda: (_ for _ in ()).throw(RuntimeError("boom"))}
    facts = {
        "capacity": {"peakCuPct": 210.0, "throttleMinutes": 5.0, "peakAt": "2026-08-05T13:52:00Z"},
        "items": [],
        "events": [{"user": "a@x", "cuSeconds": 500.0, "ts": "2026-08-05T13:53:00Z"}],
    }
    collector = {"collect": lambda: facts}

    res = run_tier2_check(collector, delivery_sinks=None, alerts_store=None,
                          now_dt=T0, config={}, baseline_store=bad_store)
    # Sweep completed, capacity triggers still fired, no crash.
    assert res["triggered"] is True
    caps = [t for t in res["triggers"] if t["check"] in ("throttle", "pressure",
                                                           "extreme_peak")]
    assert caps                                            # capacity signals unaffected
    # No spikes correlated (the detector fell back to silence when the store raised)
    assert not any(t.get("correlatedUserSpikes") for t in res["triggers"])
