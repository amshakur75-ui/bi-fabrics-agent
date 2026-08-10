"""Design A' (2026-08-09) — additional capacity detectors beyond throttle/pressure/overage.

Covers the two new Tier-2 checks introduced so Design A's coverage picks up capacity events that
Fabric's smoothing absorbs (`extreme_peak`) or that the platform is approaching but has not yet
crossed (`throttle_imminent`). Detector + integration surfaces (title/facts/severity/materiality/
incident-key) are pinned so a fired signal produces a well-formed, deduped alert the same way
the existing capacity checks do.
"""
from datetime import datetime, timezone

from fabric_audit_agent.automation.tier2_check import (
    _check_extreme_peak, _check_throttle_imminent, _title_for, _facts_for,
)
from fabric_audit_agent.automation.incident import (
    incident_key, severity_of, primary_metric,
)
from fabric_audit_agent.automation.materiality import classify, is_escalation, load_cfg


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


def test_throttle_imminent_fires_at_or_above_threshold():
    # any one Fabric threshold pct >= 80% triggers the early-warning
    facts = {"capacity": {"peakCuPct": 92.0, "maxInteractiveDelayPct": 88.0,
                          "maxInteractiveRejectionPct": 40.0,
                          "maxBackgroundRejectionPct": 30.0}}
    trigs = _check_throttle_imminent(facts)
    assert len(trigs) == 1
    t = trigs[0]
    assert t["check"] == "throttle_imminent"
    assert t["worstPct"] == 88.0
    assert "interactiveDelay" in t["thresholdPcts"]
    # only the breached pcts are surfaced — the sub-threshold ones do not clutter the alert
    assert "interactiveRejection" not in t["thresholdPcts"]
    assert "background" not in t["thresholdPcts"]


def test_throttle_imminent_reports_worst_across_signals():
    facts = {"capacity": {"maxInteractiveDelayPct": 82.0, "maxInteractiveRejectionPct": 95.0,
                          "maxBackgroundRejectionPct": 81.0}}
    trigs = _check_throttle_imminent(facts)
    assert trigs[0]["worstPct"] == 95.0
    # all three qualifying signals surface
    assert set(trigs[0]["thresholdPcts"].keys()) == {"interactiveDelay", "interactiveRejection",
                                                      "background"}


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
    # both are capacity-wide (no workspace/item scope) — one key per capacity, dedupes across runs
    assert incident_key(ep) == "extreme_peak::capacity"
    assert incident_key(ti) == "throttle_imminent::capacity"


def test_new_checks_derived_severity():
    # a fired extreme_peak (>= 200% by default) is warn by definition
    assert severity_of({"check": "extreme_peak", "peakCuPct": 220.0}) == "warn"
    # throttle_imminent worst-case severity gates on the worst pct (>= 90% = warn)
    assert severity_of({"check": "throttle_imminent", "worstPct": 85.0}) == "info"
    assert severity_of({"check": "throttle_imminent", "worstPct": 92.0}) == "warn"


def test_primary_metric_wired():
    assert primary_metric({"check": "extreme_peak", "peakCuPct": 220.0}) == 220.0
    assert primary_metric({"check": "throttle_imminent", "worstPct": 88.0}) == 88.0


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
    # throttle_imminent uses the same peak-delta rule
    assert is_escalation({"check": "throttle_imminent", "worstPct": 92.0},
                         {"severity": "info", "metric": 82.0}, cfg) is True


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


def test_new_checks_do_push_to_teams():
    # unlike attribution (concentration / cross_user) which is notification-center only, both
    # new capacity signals are hard capacity emergencies — they belong on Teams alongside
    # throttle/pressure/overage.
    from fabric_audit_agent.automation.tier2_check import process_alerts
    from fabric_audit_agent.context_alerts import create_alerts_store_memory
    T0 = datetime(2026, 8, 9, 10, 0, 0, tzinfo=timezone.utc)
    store = create_alerts_store_memory()
    posts, sink = _sink()
    trig = {"check": "extreme_peak", "peakCuPct": 250.0}
    a = process_alerts([trig], now_dt=T0, alerts_store=store, delivery_sinks={"webhook": sink},
                       reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True},
                       app_url="https://app")
    assert a["new"] == ["extreme_peak::capacity"]
    assert len(posts) == 1                                  # single Teams card
