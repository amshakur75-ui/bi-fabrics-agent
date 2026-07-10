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


# ---- enabled path: gate runs before the sink ----
def test_email_notify_gates_secret_before_sink():
    captured, sink = _capturing_sink()
    payload = _envelope(findings=[{"key": "capacity.throttle::c", "clientSecret": "s3cr3t"}])
    out = dispatch_outbound("email_notify", payload, sinks={"email": sink})
    assert out["dispatched"] is True
    delivered = captured[0]
    assert delivered["data"]["findings"][0]["clientSecret"] == "***"   # masked at the sink


def test_email_notify_injects_disclosure_when_content_withheld():
    captured, sink = _capturing_sink()
    payload = _envelope(
        findings=[{"key": "k"}],
        secretHolder={"sensitivityLabel": "Confidential", "value": "x"},
    )
    out = dispatch_outbound("email_notify", payload, sinks={"email": sink})
    assert out["disclosure"] and "withheld" in out["disclosure"]
    assert "withheld" in captured[0]["summary"]   # carried into the delivered summary


def test_email_notify_no_spurious_disclosure_on_clean_payload():
    captured, sink = _capturing_sink()
    payload = _envelope(findings=[{"key": "k", "what": "fine"}])
    out = dispatch_outbound("email_notify", payload, sinks={"email": sink})
    assert out["disclosure"] is None
    assert captured[0]["summary"] == "audit"   # unchanged, no trailing "(...)"


# ---- refusals ----
def test_teams_notify_refused_disabled():
    captured, sink = _capturing_sink()
    out = dispatch_outbound("teams_notify", _envelope(), sinks={"teams": sink})
    assert out["dispatched"] is False and "Phase 7" in out["reason"]
    assert captured == []   # nothing sent


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


def test_enabled_type_missing_sink_refused_no_raise():
    out = dispatch_outbound("email_notify", _envelope(), sinks={})
    assert out["dispatched"] is False and out["reason"] == "no sink provided"


# ---- purity + invariant ----
def test_caller_payload_not_mutated():
    _, sink = _capturing_sink()
    payload = _envelope(findings=[{"key": "k", "clientSecret": "s3cr3t"}])
    before = copy.deepcopy(payload)
    dispatch_outbound("email_notify", payload, sinks={"email": sink})
    assert payload == before   # deep-copied inside the gate; caller object untouched


def test_delivered_reflects_sink_noop():
    # A sink reporting {"delivered": False} (e.g. unconfigured email) → dispatched True, delivered False.
    noop_sink = {"deliver": lambda e: {"delivered": False, "reason": "unconfigured"}}
    out = dispatch_outbound("email_notify", _envelope(), sinks={"email": noop_sink})
    assert out["dispatched"] is True and out["delivered"] is False


def test_delivered_true_when_sink_sends():
    _, sink = _capturing_sink()   # returns None -> assumed delivered
    out = dispatch_outbound("email_notify", _envelope(), sinks={"email": sink})
    assert out["dispatched"] is True and out["delivered"] is True


def test_registry_has_no_data_mutating_action_type():
    # Outbound is surface-only: nothing that writes/scales/refreshes/deletes may be registrable.
    forbidden = ("delete", "scale", "refresh", "write", "update", "restart", "resume", "pause")
    for name in _ALLOWLIST:
        assert not any(word in name for word in forbidden), name
