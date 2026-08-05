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
    assert incident_key({"check": "throttle"}) == "throttle::capacity"
    assert incident_key({"check": "pressure"}) == "pressure::capacity"


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


def test_attribution_severity_floor_no_info_cards():
    # Step 2 anti-flapping: attribution signals only card at Warning+ (or when recurring). An
    # Info-level concentration/cross-user is suppressed (logged only), never a standalone card.
    assert classify({"check": "concentration", "sharePct": 55}, CFG)[0] == "report"   # warn (>=50)
    assert classify({"check": "concentration", "sharePct": 45}, CFG)[0] == "suppress"  # info -> floor
    assert classify({"check": "concentration", "sharePct": 31}, CFG)[0] == "suppress"
    assert classify({"check": "cross_user", "userCount": 4}, CFG)[0] == "report"       # warn (>=4)
    assert classify({"check": "cross_user", "userCount": 3}, CFG)[0] == "suppress"     # info -> floor
    # recurring still cards even below the Warning cutoff
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
