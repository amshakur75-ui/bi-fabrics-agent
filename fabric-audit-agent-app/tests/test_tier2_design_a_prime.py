"""Design A' (2026-08-09) — additional capacity detectors beyond throttle/pressure/overage.

Covers `extreme_peak` (capacity events Fabric's smoothing absorbs) and pins that
`throttle_imminent` stays RETIRED — it was built on a misread field (see
`_check_throttle_imminent`'s docstring) and must remain a no-op. Detector + integration surfaces (title/facts/severity/materiality/
incident-key) are pinned so a fired signal produces a well-formed, deduped alert the same way
the existing capacity checks do.
"""
from datetime import datetime, timedelta, timezone

from fabric_audit_agent.automation.tier2_check import (
    _check_extreme_peak, _check_throttle_imminent, _title_for, _facts_for, process_alerts,
)
from fabric_audit_agent.automation.incident import (
    incident_key, severity_of, primary_metric,
)
from fabric_audit_agent.automation.materiality import classify, is_escalation, load_cfg
from fabric_audit_agent.context_alerts import create_alerts_store_memory


def test_extreme_peak_fires_at_or_above_threshold():
    # default extreme_peak_pct = 200
    facts = {"capacity": {"peakCuPct": 250.0, "throttleMinutes": 0.0, "capacityId": "cap-A"}}
    trigs = _check_extreme_peak(facts)
    assert len(trigs) == 1
    t = trigs[0]
    assert t["check"] == "extreme_peak"
    assert t["peakCuPct"] == 250.0
    assert t["capacityId"] == "cap-A"
    assert "extremeThreshold" in t


def test_extreme_peak_silent_below_threshold_even_when_pressured():
    # 180% is pressure, not extreme — pressure check owns that firing
    assert _check_extreme_peak({"capacity": {"peakCuPct": 180.0}}) == []


def test_extreme_peak_silent_when_peak_missing():
    assert _check_extreme_peak({"capacity": {}}) == []
    assert _check_extreme_peak({}) == []


def test_extreme_peak_respects_config_override():
    cfg = load_cfg()
    cfg["extreme_peak_pct"] = 150.0
    # 175 is over the lowered threshold but below default 200 — the config wins
    assert _check_extreme_peak({"capacity": {"peakCuPct": 175.0}}, cfg) != []
    assert _check_extreme_peak({"capacity": {"peakCuPct": 140.0}}, cfg) == []




def test_throttle_imminent_is_RETIRED_and_always_silent():
    """Built on a misread field: the *ThresholdPercentage columns are the throttle SETTING
    (kb metric_type "reference", "confirmed constant = 1"), not a live utilization. The live
    value scales to 123.71, so `>= 80` was true on EVERY window — one permanent warn incident
    firing 288x/day, whose all-time high-water metric would also poison the peak-escalation axis
    for that capacity forever. The sound early-warning is _check_sustained_band (real CU% in a
    sub-100 band). Must stay a no-op."""
    facts = {"capacity": {"peakCuPct": 92.0, "maxInteractiveDelayPct": 123.71,
                          "maxInteractiveRejectionPct": 150.0,
                          "maxBackgroundRejectionPct": 200.0}}
    assert _check_throttle_imminent(facts) == []


def test_throttle_imminent_silent_when_no_threshold_data():
    assert _check_throttle_imminent({"capacity": {"peakCuPct": 65.0}}) == []
    assert _check_throttle_imminent({"capacity": {}}) == []


def test_throttle_imminent_silent_below_threshold():
    assert _check_throttle_imminent({"capacity": {"maxInteractiveDelayPct": 60.0,
                                                   "maxInteractiveRejectionPct": 70.0,
                                                   "maxBackgroundRejectionPct": 78.0}}) == []


def test_new_checks_have_stable_incident_keys():
    ep = {"check": "extreme_peak", "peakCuPct": 220.0}
    ti = {"check": "throttle_imminent", "worstPct": 88.0}
    # Design A': all five capacity-family checks share ONE key per capacity so multi-signal
    # firings for the same incident coalesce (one card, not N).
    assert incident_key(ep) == "capacity::capacity"
    assert incident_key(ti) == "capacity::capacity"


def test_new_checks_derived_severity():
    # a fired extreme_peak (>= 200% by default) is warn by definition
    assert severity_of({"check": "extreme_peak", "peakCuPct": 220.0}) == "warn"
    # throttle_imminent worst-case severity gates on the worst pct (>= 90% = warn)
    assert severity_of({"check": "throttle_imminent", "worstPct": 85.0}) == "info"
    assert severity_of({"check": "throttle_imminent", "worstPct": 92.0}) == "warn"


def test_primary_metric_is_unit_stable_across_the_capacity_family():
    """Design A' shares ONE incident key across five capacity checks, so the stored ``metric``
    must have ONE unit or escalation compares minutes against percent. Every capacity-family
    check therefore reports peakCuPct; throttle severity rides its own ``throttleMinutes``
    axis instead of being crammed into the same scalar."""
    assert primary_metric({"check": "extreme_peak", "peakCuPct": 220.0}) == 220.0
    assert primary_metric({"check": "pressure", "peakCuPct": 130.0}) == 130.0
    # throttle used to report MINUTES here — that is what made a 2.0-minute throttle followed
    # by a 110% pressure read as a "+108 point" escalation.
    assert primary_metric({"check": "throttle", "throttleMinutes": 8.0,
                           "peakCuPct": 130.0}) == 130.0
    # throttle_imminent's worstPct is a THRESHOLD percentage, not CU% — not interchangeable,
    # so it no longer leaks into the shared metric.
    assert primary_metric({"check": "throttle_imminent", "worstPct": 88.0,
                           "peakCuPct": 92.0}) == 92.0
    assert primary_metric({"check": "throttle_imminent", "worstPct": 88.0}) is None


def test_materiality_always_reports_a_fired_new_signal():
    # detectors only fire when >= threshold, so every fired trigger is by definition material
    d, _ = classify({"check": "extreme_peak", "peakCuPct": 210.0})
    assert d == "report"
    d, _ = classify({"check": "throttle_imminent", "worstPct": 85.0})
    assert d == "report"


def test_escalation_worsens_only_when_metric_climbs():
    cfg = load_cfg()
    cfg["esc_peak_delta"] = 20.0
    # extreme_peak: 210 -> 235 = +25 pts >= delta, escalates
    assert is_escalation({"check": "extreme_peak", "peakCuPct": 235.0},
                         {"severity": "warn", "metric": 210.0}, cfg) is True
    # 210 -> 215 = +5 pts, below delta, no escalation
    assert is_escalation({"check": "extreme_peak", "peakCuPct": 215.0},
                         {"severity": "warn", "metric": 210.0}, cfg) is False
    # throttle_imminent crossing 90% promotes info -> warn, so it escalates on the
    # SEVERITY-RANK rule (not the peak delta — worstPct is a threshold pct, not CU%).
    assert is_escalation({"check": "throttle_imminent", "worstPct": 92.0},
                         {"severity": "info", "metric": 82.0}, cfg) is True


def test_escalation_ignores_new_signal_rule_when_prior_set_unknown():
    """Migration guard: rows already in the prod alerts table were written before
    ``signalTypes`` existed, so they read back as absent. Absent must mean UNKNOWN, not
    "no signals" — otherwise every signal looks new and a card fires every 5 minutes,
    which is the exact failure the rule exists to prevent."""
    cfg = load_cfg()
    cfg["esc_peak_delta"] = 20.0
    legacy_prior = {"severity": "warn", "metric": 130.0}          # no signalTypes key
    assert is_escalation({"check": "pressure", "peakCuPct": 132.0},
                         legacy_prior, cfg) is False
    # Once the prior DOES record a set, a genuinely new signal escalates.
    known_prior = {"severity": "warn", "metric": 130.0, "signalTypes": ["pressure"]}
    assert is_escalation({"check": "capacity_incident", "peakCuPct": 132.0,
                          "signalTypes": ["pressure", "throttle"]},
                         known_prior, cfg) is True


def test_escalation_fires_when_throttling_starts_on_a_pressure_incident():
    """The unit-mixing bug's worst symptom: a real throttle arriving after a high-percentage
    pressure reading used to be SILENT, because `30 >= max(5, 2*250)` is False. Throttle now
    has its own axis, so throttling starting is always an escalation."""
    cfg = load_cfg()
    prior = {"severity": "warn", "metric": 250.0, "signalTypes": ["pressure", "extreme_peak"],
             "throttleMinutes": None}
    trigger = {"check": "capacity_incident", "peakCuPct": 240.0, "throttleMinutes": 30.0,
               "signalTypes": ["pressure", "extreme_peak"]}
    assert is_escalation(trigger, prior, cfg) is True


def test_titles_are_human_readable():
    assert "Extreme CU peak" in _title_for({"check": "extreme_peak", "peakCuPct": 220.0})
    assert "Throttle imminent" in _title_for({"check": "throttle_imminent", "worstPct": 88.0})


def test_facts_surface_the_signal_shape():
    ep = _facts_for({"check": "extreme_peak", "peakCuPct": 220.0, "extremeThreshold": 200.0})
    assert any(n == "Peak CU" and v == "220.0%" for n, v in ep)
    assert any(n == "Extreme threshold" for n, _ in ep)
    ti = _facts_for({"check": "throttle_imminent", "worstPct": 88.0, "peakCuPct": 92.0,
                     "thresholdPcts": {"interactiveDelay": 88.0, "background": 82.0}})
    names = [n for n, _ in ti]
    assert "Worst threshold pct" in names and "Peak CU" in names and "Signals" in names


def _sink():
    posts = []
    return posts, {"deliver": lambda b: (posts.append(b), {"delivered": True, "status": 202})[1]}


def test_coalesce_leaves_single_signal_alone():
    """One capacity-family trigger passes through unchanged — no composite wrapping when there's
    nothing to merge. Non-capacity triggers pass through untouched too."""
    from fabric_audit_agent.automation.tier2_check import _coalesce_capacity_family
    trigs = [{"check": "throttle", "throttleMinutes": 5.0, "peakCuPct": 105.0},
             {"check": "concentration", "sharePct": 42.0, "item": "X", "workspace": "W"}]
    out = _coalesce_capacity_family(trigs)
    checks = [t["check"] for t in out]
    assert checks == ["throttle", "concentration"]


def test_coalesce_merges_multiple_capacity_signals_into_one_composite():
    """The core Aug 5 case: throttle + pressure + extreme_peak fire in the same run for the
    same underlying event -> ONE capacity_incident, not three separate cards. The composite
    carries a signalTypes list and hoists the primary metrics from whichever component
    surfaced them."""
    from fabric_audit_agent.automation.tier2_check import _coalesce_capacity_family
    trigs = [{"check": "throttle", "throttleMinutes": 8.5, "peakCuPct": 210.0},
             {"check": "pressure", "peakCuPct": 210.0},
             {"check": "extreme_peak", "peakCuPct": 210.0}]
    out = _coalesce_capacity_family(trigs)
    assert len(out) == 1
    c = out[0]
    assert c["check"] == "capacity_incident"
    assert set(c["signalTypes"]) == {"throttle", "pressure", "extreme_peak"}
    assert c["peakCuPct"] == 210.0
    assert c["throttleMinutes"] == 8.5



def test_coalesce_partitions_by_capacity_id():
    """Two capacities firing simultaneously get two composites — the coalescing is per-capacity,
    not per-run. In our deployment there is one capacity, but the design must handle N."""
    from fabric_audit_agent.automation.tier2_check import _coalesce_capacity_family
    trigs = [{"check": "throttle", "throttleMinutes": 5.0, "capacityId": "cap-A"},
             {"check": "pressure", "peakCuPct": 130.0, "capacityId": "cap-A"},
             {"check": "throttle", "throttleMinutes": 2.0, "capacityId": "cap-B"},
             {"check": "extreme_peak", "peakCuPct": 220.0, "capacityId": "cap-B"}]
    out = _coalesce_capacity_family(trigs)
    assert len(out) == 2
    ids = sorted(t["capacityId"] for t in out)
    assert ids == ["cap-A", "cap-B"]


def test_composite_title_names_all_signals():
    """A composite title lists every signal firing plus the peak — one glance tells the
    reader everything happening for this incident."""
    from fabric_audit_agent.automation.tier2_check import _title_for
    c = {"check": "capacity_incident", "signalTypes": ["throttle", "pressure", "extreme_peak"],
         "peakCuPct": 210.0}
    title = _title_for(c)
    assert "Capacity incident" in title
    assert "over budget" in title and "CU pressure" in title and "extreme peak" in title
    assert "210.0%" in title


def test_composite_facts_are_signal_bag_plus_hoisted_metrics():
    """Card facts on a composite show the signal set + every hoisted primary metric — the
    single card replaces N separate cards."""
    from fabric_audit_agent.automation.tier2_check import _facts_for
    c = {"check": "capacity_incident", "signalTypes": ["throttle", "pressure"],
         "peakCuPct": 210.0, "throttleMinutes": 8.5,
         "thresholdPcts": {"interactiveDelay": 88.0}}
    fmap = dict(_facts_for(c))
    assert "Signals firing" in fmap and "over budget" in fmap["Signals firing"]
    assert fmap["Peak CU"] == "210.0%"
    assert fmap["Over budget"] == "8.5 min at >100% CU"
    assert "Fabric thresholds" in fmap


def test_composite_severity_takes_the_max_across_signals():
    """One warn-severity component in the composite promotes the whole composite to warn."""
    from fabric_audit_agent.automation.incident import severity_of
    # pressure alone at 105% is info; add throttle at 8 min (warn) -> composite goes warn
    c = {"check": "capacity_incident",
         "signals": [{"check": "pressure", "peakCuPct": 105.0},
                     {"check": "throttle", "throttleMinutes": 8.0}]}
    assert severity_of(c) == "warn"
    # two info-only components stay info
    c2 = {"check": "capacity_incident",
          "signals": [{"check": "pressure", "peakCuPct": 105.0},
                      {"check": "throttle_imminent", "worstPct": 82.0}]}
    assert severity_of(c2) == "info"


def test_composite_escalation_fires_when_new_signal_joins():
    """Design A' escalation rule: a new signal type joining the incident (e.g. pressure crosses
    into throttle) IS a genuine worsening even when scalar metrics didn't move."""
    from fabric_audit_agent.automation.materiality import is_escalation, load_cfg
    cfg = load_cfg()
    new = {"check": "capacity_incident", "signalTypes": ["throttle", "pressure"],
           "peakCuPct": 120.0, "throttleMinutes": 3.0}
    prior = {"severity": "info", "metric": 120.0, "signalTypes": ["pressure"],
             "throttleMinutes": None}
    assert is_escalation(new, prior, cfg) is True
    # same signal set + no metric worsening = NOT escalation (the "silent" branch)
    prior2 = {"severity": "warn", "metric": 120.0, "signalTypes": ["throttle", "pressure"],
              "throttleMinutes": 3.0}
    assert is_escalation(new, prior2, cfg) is False


def test_end_to_end_multi_signal_produces_one_teams_card():
    """The full Aug 5 case: throttle + pressure + extreme_peak arrive in the same run.
    process_alerts must emit ONE composite Teams card, not three. The row's checkType and
    incident key both reflect the composite identity."""
    from fabric_audit_agent.automation.tier2_check import process_alerts
    from fabric_audit_agent.context_alerts import create_alerts_store_memory
    T0 = datetime(2026, 8, 9, 13, 52, 0, tzinfo=timezone.utc)
    store = create_alerts_store_memory()
    posts, sink = _sink()
    trigs = [{"check": "throttle", "throttleMinutes": 8.5, "peakCuPct": 210.0},
             {"check": "pressure", "peakCuPct": 210.0},
             {"check": "extreme_peak", "peakCuPct": 210.0}]
    a = process_alerts(trigs, now_dt=T0, alerts_store=store, delivery_sinks={"webhook": sink},
                       reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True},
                       app_url="https://app")
    assert a["new"] == ["capacity::capacity"]                     # one incident key
    assert len(posts) == 1                                        # ONE Teams card, not three
    row = store["query_active"]()["capacity::capacity"]
    assert row["checkType"] == "capacity_incident"
    assert set(row["signalTypes"]) == {"throttle", "pressure", "extreme_peak"}


def _quiet_cfg(**overrides):
    """cfg with hysteresis disabled + quiet_ticks whatever the test wants (default 3 for speed)."""
    c = load_cfg()
    c["hysteresis_ticks"] = 1
    c["quiet_ticks"] = 3
    c.update(overrides)
    return c


def test_capacity_incident_holds_open_through_brief_absence():
    """A capacity incident that clears for one tick MUST NOT auto-resolve — it stays as the
    same incident (currentlyActive=False during the grace period, no new Teams card if it
    re-fires). This is the resolve-then-re-fire flap the Design A' quiet_ticks knob fixes."""
    T0 = datetime(2026, 8, 9, 13, 52, 0, tzinfo=timezone.utc)
    store = create_alerts_store_memory()
    posts, sink = _sink()
    kw = dict(alerts_store=store, delivery_sinks={"webhook": sink},
              reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True},
              app_url="https://app", cfg=_quiet_cfg(quiet_ticks=3))
    trig = {"check": "throttle", "throttleMinutes": 5.0, "peakCuPct": 105.0}

    process_alerts([trig], now_dt=T0, **kw)                                # fire
    assert len(posts) == 1
    process_alerts([], now_dt=T0 + timedelta(minutes=5), **kw)             # absence 1
    process_alerts([], now_dt=T0 + timedelta(minutes=10), **kw)            # absence 2
    active = store["query_active"]()
    # Row is still open (not resolved), just marked currentlyActive=False during the grace.
    assert "capacity::capacity" in active
    assert active["capacity::capacity"]["absenceCount"] == 2
    assert active["capacity::capacity"]["currentlyActive"] is False


def test_capacity_incident_resolves_after_quiet_ticks_expire():
    """Once absent for quiet_ticks consecutive sweeps the incident auto-resolves — the
    fabric state actually cleared and the ticket lifecycle should reflect it."""
    T0 = datetime(2026, 8, 9, 13, 52, 0, tzinfo=timezone.utc)
    store = create_alerts_store_memory()
    posts, sink = _sink()
    kw = dict(alerts_store=store, delivery_sinks={"webhook": sink},
              reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True},
              app_url="https://app", cfg=_quiet_cfg(quiet_ticks=3))
    trig = {"check": "throttle", "throttleMinutes": 5.0, "peakCuPct": 105.0}

    process_alerts([trig], now_dt=T0, **kw)                                # fire
    for i in range(1, 3):                                                   # absences 1, 2
        process_alerts([], now_dt=T0 + timedelta(minutes=5 * i), **kw)
    # third absence hits quiet_ticks — auto-resolve fires now
    a = process_alerts([], now_dt=T0 + timedelta(minutes=15), **kw)
    assert a["resolved"] == ["capacity::capacity"]
    assert "capacity::capacity" not in store["query_active"]()
    # still exactly ONE Teams card ever — the auto-resolve is silent (no Resolved card)
    assert len(posts) == 1


def test_refire_during_grace_stays_same_incident_no_new_card():
    """The Aug 5 shape: a capacity event clears for 1-2 sweeps, then re-fires. Under Design A'
    that stays the SAME incident (same key, no new Teams card), not a resolve-then-new-card
    flap. Escalation logic still applies if the metrics genuinely worsened, but a same-shape
    re-fire is silent."""
    T0 = datetime(2026, 8, 9, 13, 52, 0, tzinfo=timezone.utc)
    store = create_alerts_store_memory()
    posts, sink = _sink()
    kw = dict(alerts_store=store, delivery_sinks={"webhook": sink},
              reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True},
              app_url="https://app", cfg=_quiet_cfg(quiet_ticks=6))
    trig = {"check": "throttle", "throttleMinutes": 5.0, "peakCuPct": 105.0}

    process_alerts([trig], now_dt=T0, **kw)                                # fire (1 card)
    process_alerts([], now_dt=T0 + timedelta(minutes=5), **kw)             # absent, grace=1
    process_alerts([], now_dt=T0 + timedelta(minutes=10), **kw)            # absent, grace=2
    # re-fire with the same shape while still in grace
    a = process_alerts([trig], now_dt=T0 + timedelta(minutes=15), **kw)
    assert a["silent"] == ["capacity::capacity"]                            # same incident
    assert len(posts) == 1                                                  # still one card
    row = store["query_active"]()["capacity::capacity"]
    assert row["absenceCount"] == 0 and row["currentlyActive"] is True     # grace clock reset


def test_refire_after_quiet_period_is_a_fresh_incident():
    """Once the incident actually resolves (quiet_ticks elapsed), a later re-fire is a NEW
    incident — a fresh Teams card. This is the correct behavior: the capacity was clean for
    a full quiet window; the return is a distinct event."""
    T0 = datetime(2026, 8, 9, 13, 52, 0, tzinfo=timezone.utc)
    store = create_alerts_store_memory()
    posts, sink = _sink()
    kw = dict(alerts_store=store, delivery_sinks={"webhook": sink},
              reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True},
              app_url="https://app", cfg=_quiet_cfg(quiet_ticks=2))
    trig = {"check": "throttle", "throttleMinutes": 5.0, "peakCuPct": 105.0}

    process_alerts([trig], now_dt=T0, **kw)                                # fire (1 card)
    process_alerts([], now_dt=T0 + timedelta(minutes=5), **kw)             # absence 1
    process_alerts([], now_dt=T0 + timedelta(minutes=10), **kw)            # absence 2 -> resolved
    assert "capacity::capacity" not in store["query_active"]()

    # a later re-fire is a fresh incident + a fresh card
    a = process_alerts([trig], now_dt=T0 + timedelta(minutes=60), **kw)
    assert a["new"] == ["capacity::capacity"]
    assert len(posts) == 2                                                  # two distinct cards


def test_new_checks_do_push_to_teams():
    # unlike attribution (concentration / cross_user) which is notification-center only, both
    # new capacity signals are hard capacity emergencies — they belong on Teams alongside
    # throttle/pressure/overage.
    T0 = datetime(2026, 8, 9, 10, 0, 0, tzinfo=timezone.utc)
    store = create_alerts_store_memory()
    posts, sink = _sink()
    trig = {"check": "extreme_peak", "peakCuPct": 250.0}
    a = process_alerts([trig], now_dt=T0, alerts_store=store, delivery_sinks={"webhook": sink},
                       reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True},
                       app_url="https://app")
    assert a["new"] == ["capacity::capacity"]
    assert len(posts) == 1                                  # single Teams card
