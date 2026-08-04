"""Tests for the Phase 6 typed outbound allowlist (outbound.py). Offline."""
import copy

from fabric_audit_agent.outbound import dispatch_outbound, _ALLOWLIST


def _capturing_sink():
    captured = []
    return captured, {"deliver": lambda e: captured.append(e)}


def _envelope(**data):
    d = {"tenant": "Acme"}
    d.update(data)
    return {"summary": "audit", "data": d}


# ---- refusals (all delivery types removed; only ado_create_ticket remains, disabled) ----
def test_ado_create_ticket_refused_disabled():
    captured, sink = _capturing_sink()
    out = dispatch_outbound("ado_create_ticket", _envelope(), sinks={"ticket": sink})
    assert out["dispatched"] is False
    assert captured == []


def test_unknown_action_type_refused():
    captured, sink = _capturing_sink()
    out = dispatch_outbound("delete_capacity", _envelope(), sinks={"email": sink})
    assert out["dispatched"] is False and out["reason"] == "unknown action type"
    assert captured == []


def test_teams_notify_refused_after_delivery_removal():
    out = dispatch_outbound("teams_notify", _envelope(), sinks={"teams": _capturing_sink()[1]})
    assert out["dispatched"] is False and out["reason"] == "unknown action type"


def test_email_notify_refused_after_delivery_removal():
    out = dispatch_outbound("email_notify", _envelope(), sinks={"email": _capturing_sink()[1]})
    assert out["dispatched"] is False and out["reason"] == "unknown action type"


def test_allowlist_is_closed_to_known_entries():
    # Closed by construction: ado (disabled placeholder) + the one ENABLED read-only
    # notification type (tier2_alert, webhook sink) added in sub-project #2.
    assert set(_ALLOWLIST) == {"ado_create_ticket", "tier2_alert"}
    assert _ALLOWLIST["ado_create_ticket"]["enabled"] is False
    assert _ALLOWLIST["tier2_alert"] == {"enabled": True, "sink": "webhook"}


# ---- purity + invariant ----
def test_registry_has_no_data_mutating_action_type():
    forbidden = ("delete", "scale", "refresh", "write", "update", "restart", "resume", "pause")
    for name in _ALLOWLIST:
        assert not any(word in name for word in forbidden), name
