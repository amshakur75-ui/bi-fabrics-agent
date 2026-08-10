"""Round-3 audit: every silent degradation must reach a human, and must FAIL CLOSED.

The bug class these pin down is not a crash — it is a run that reports success while having
accomplished nothing, or a gate that cannot run yet claims it did. Each test names the surface a
human would actually have looked at.
"""
import pytest

from fabric_audit_agent import egress, outbound, pipeline
from fabric_audit_agent.automation.health import HealthReport


# ---- outbound: the delivery REPORT must fail closed ------------------------

def _sink(ret):
    return {"webhook": {"deliver": lambda body: ret}}


def test_delivery_report_defaults_to_not_delivered():
    """A sink that returns a non-dict, or a dict without the key, means WE DO NOT KNOW whether a
    human was reached. Defaulting that to True recorded an undelivered capacity card as delivered
    and defeated every caller that inspects ``delivered`` to decide whether to retry."""
    assert outbound.dispatch_outbound(
        "tier2_alert", {"summary": "s"}, sinks=_sink(None))["delivered"] is False
    assert outbound.dispatch_outbound(
        "tier2_alert", {"summary": "s"}, sinks=_sink("1"))["delivered"] is False
    assert outbound.dispatch_outbound(
        "tier2_alert", {"summary": "s"}, sinks=_sink({}))["delivered"] is False


def test_delivery_report_honours_an_explicit_success():
    res = outbound.dispatch_outbound("tier2_alert", {"summary": "s"},
                                     sinks=_sink({"delivered": True, "status": 200}))
    assert res["delivered"] is True and res["dispatched"] is True


def test_explicit_http_failure_is_not_delivered():
    res = outbound.dispatch_outbound("tier2_alert", {"summary": "s"},
                                     sinks=_sink({"delivered": False, "status": 500}))
    assert res["delivered"] is False


# ---- egress: a cap that cannot run must not claim nothing was omitted ------

def test_size_cap_failure_discloses_truncation_rather_than_denying_it(monkeypatch):
    """The old handler swallowed the error, leaving the payload UNCAPPED while truncated stayed
    False and rowsOmitted stayed 0 — so disclosure_line() returned None and the card positively
    asserted nothing had been dropped."""
    def boom(rows, max_chars=None):
        raise RuntimeError("cap exploded")
    monkeypatch.setattr(egress, "cap_rows", boom)
    # cap_rows is reached via data.findings (or a top-level list), not a bare "findings" key.
    _safe, meta = egress.apply_egress_controls(
        {"data": {"findings": [{"a": 1}]}}, sink="alert")
    assert meta["truncated"] is True
    assert "cap exploded" in meta["capError"]
    assert egress.disclosure_line(meta) is not None   # the card now DISCLOSES instead of denying


# ---- pipeline: a lost findings-history write degrades recurrence silently --

def test_findings_write_failure_is_announced(capsys):
    """audit_findings is what recurrence detection reads. Losing writes silently means
    isRecurring is never true, which demotes genuinely recurring triggers to `ambiguous` and flips
    recurring attribution patterns back into live tickets — while the sweep prints
    "Audit complete: N findings" and exits 0."""
    def boom(run_at, tenant, findings):
        raise RuntimeError("delta down")

    collector = {"collect": lambda: {"capacity": {"peakCuPct": 50.0}, "items": []}}
    reasoner = {"reason": lambda facts, flags: flags}
    delivery = {"deliver": lambda payload: {"delivered": True}}
    pipeline.run_audit(collector, reasoner, delivery,
                       findings_store={"write": boom})
    out = capsys.readouterr().out
    assert "findings history write FAILED" in out
    assert "delta down" in out


# ---- HealthReport is the shared surface all of the above feed -------------

def test_health_report_degrades_on_a_failed_delivery():
    h = HealthReport()
    h.record_delivery("webhook", False, "status=500")
    assert h.degraded is True
    assert "webhook" in h.summary
