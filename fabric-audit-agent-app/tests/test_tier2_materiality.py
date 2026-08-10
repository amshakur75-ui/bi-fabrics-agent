"""Tier-2 alert materiality + incident identity (pure logic, no I/O)."""
from fabric_audit_agent.automation.incident import (
    incident_key, severity_of, primary_metric,
)
from fabric_audit_agent.automation.materiality import (
    classify, is_escalation, load_cfg,
)

CFG = load_cfg({})  # defaults


# ---- incident_key --------------------------------------------------------
def test_incident_key_concentration_is_item_scoped():
    t = {"check": "concentration", "workspace": "WS", "item": "Model A"}
    assert incident_key(t) == "concentration::WS/Model A"


def test_incident_key_capacity_scoped():
    # Design A' (2026-08-09): all five capacity-family checks share ONE key per capacity so
    # multi-signal firings for the same incident coalesce into one Teams card, not N.
    assert incident_key({"check": "throttle"}) == "capacity::capacity"
    assert incident_key({"check": "pressure"}) == "capacity::capacity"
    assert incident_key({"check": "overage"}) == "capacity::capacity"
    assert incident_key({"check": "extreme_peak"}) == "capacity::capacity"
    assert incident_key({"check": "throttle_imminent"}) == "capacity::capacity"
    # A composite trigger produced by _coalesce_capacity_family shares the same key.
    assert incident_key({"check": "capacity_incident"}) == "capacity::capacity"


def test_incident_key_stable_across_dict_order():
    a = {"check": "concentration", "item": "X", "workspace": "W"}
    b = {"workspace": "W", "check": "concentration", "item": "X"}
    assert incident_key(a) == incident_key(b)


# ---- severity_of ---------------------------------------------------------
def test_severity_derived_from_metrics():
    assert severity_of({"check": "throttle", "throttleMinutes": 8}) == "warn"
    assert severity_of({"check": "throttle", "throttleMinutes": 3}) == "info"
    assert severity_of({"check": "concentration", "sharePct": 55}) == "warn"
    assert severity_of({"check": "concentration", "sharePct": 32}) == "info"
    assert severity_of({"check": "pressure", "peakCuPct": 130}) == "warn"
    assert severity_of({"check": "pressure", "peakCuPct": 110}) == "info"
    assert severity_of({"check": "overage", "minutesToBurndown": 30}) == "warn"
    assert severity_of({"check": "overage", "minutesToBurndown": 120}) == "info"


def test_primary_metric():
    assert primary_metric({"check": "pressure", "peakCuPct": 118}) == 118.0
    assert primary_metric({"check": "concentration", "sharePct": 41}) == 41.0
    assert primary_metric({"check": "data_unavailable"}) is None


# ---- classify ------------------------------------------------------------
def test_recurring_always_reports():
    t = {"check": "pressure", "peakCuPct": 101, "recurrence": {"isRecurring": True}}
    assert classify(t, CFG)[0] == "report"


def test_attribution_uses_gate_not_severity_floor():
    # Attribution materiality is decided by the GATE (share/user thresholds), and anti-flapping is
    # enforced separately by HYSTERESIS in process_alerts — NOT by a blanket Info-level severity
    # floor (an earlier floor silenced the product's whole concentration/cross-user alert stream,
    # since live attribution is almost always Info-severity). So an Info-level but material share
    # still REPORTS here; hysteresis is what makes it wait for persistence before it cards.
    assert classify({"check": "concentration", "sharePct": 55}, CFG)[0] == "report"    # warn (>=50)
    assert classify({"check": "concentration", "sharePct": 45}, CFG)[0] == "report"    # info but >=40
    assert classify({"check": "concentration", "sharePct": 36}, CFG)[0] == "ambiguous"  # 33..40 band
    assert classify({"check": "concentration", "sharePct": 31}, CFG)[0] == "suppress"  # < 33, a blip
    assert classify({"check": "cross_user", "userCount": 4}, CFG)[0] == "report"       # warn (>=4)
    assert classify({"check": "cross_user", "userCount": 3}, CFG)[0] == "report"       # detector-gated
    # recurring always reports
    assert classify({"check": "concentration", "sharePct": 45,
                     "recurrence": {"isRecurring": True}}, CFG)[0] == "report"


def test_throttle_bands():
    assert classify({"check": "throttle", "throttleMinutes": 8}, CFG)[0] == "report"
    assert classify({"check": "throttle", "throttleMinutes": 3}, CFG)[0] == "ambiguous"


def test_pressure_bands():
    assert classify({"check": "pressure", "peakCuPct": 130}, CFG)[0] == "report"
    assert classify({"check": "pressure", "peakCuPct": 112}, CFG)[0] == "ambiguous"
    assert classify({"check": "pressure", "peakCuPct": 102}, CFG)[0] == "suppress"


def test_overage_and_data_unavailable():
    assert classify({"check": "overage", "minutesToBurndown": 30}, CFG)[0] == "report"
    assert classify({"check": "overage", "minutesToBurndown": 200}, CFG)[0] == "ambiguous"
    assert classify({"check": "data_unavailable", "gate": {}}, CFG)[0] == "suppress"


# ---- is_escalation -------------------------------------------------------
def test_escalation_on_severity_rise():
    t = {"check": "pressure", "peakCuPct": 130}      # warn
    assert is_escalation(t, {"severity": "info", "metric": 105}, CFG) is True


def test_escalation_on_metric_jump():
    t = {"check": "pressure", "peakCuPct": 132}
    assert is_escalation(t, {"severity": "warn", "metric": 108}, CFG) is True   # +24
    assert is_escalation(t, {"severity": "warn", "metric": 120}, CFG) is False  # +12


def test_no_escalation_when_stable():
    t = {"check": "concentration", "sharePct": 42}
    assert is_escalation(t, {"severity": "info", "metric": 41}, CFG) is False
