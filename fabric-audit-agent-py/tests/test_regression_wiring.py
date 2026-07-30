"""Regression tests: wiring that crosses module boundaries. Offline/pure."""
import pytest
from fabric_audit_agent.pipeline import run_audit
from fabric_audit_agent.context_findings import query_recent_findings, format_context
from fabric_audit_agent.outbound import dispatch_outbound


# ---------------------------------------------------------------------------
# Test 1: End-to-end Phase 5→6 wiring (findings_store write is called)
# ---------------------------------------------------------------------------

def _fake_collector():
    return {"collect": lambda: {
        "capacity": {"baseCU": 64, "peakCuPct": 55, "throttled": False, "sku": "F64"},
        "items": [{"name": "Sales", "type": "Dataset", "sizeMB": 10}],
        "models": [],
    }}


def _fake_store():
    appended = []
    return {
        "append": lambda row: appended.append(row),
        "history": lambda keep=10: appended[-keep:] if appended else [],
    }, appended


def _fake_findings_store():
    written = []
    stored = []

    def write(run_at, tenant, findings):
        written.append({"runAt": run_at, "tenant": tenant, "findings": findings})
        for f in findings:
            stored.append({
                "findingKey": f.get("key"),
                "level": (f.get("score") or {}).get("level"),
                "whatText": f.get("what"),
                "runAt": run_at,
                "resource": f.get("where"),
                "confidence": f.get("confidence"),
            })

    def query(*, scope=None, tenant=None, limit=5):
        return stored[:limit]

    return {"query": query, "write": write}, written


class TestFindingsStoreWiring:
    def test_run_audit_calls_findings_store_write(self):
        store, _ = _fake_store()
        fs, written = _fake_findings_store()
        from fabric_audit_agent.reasoner_stub import create_stub_reasoner
        env = run_audit(
            _fake_collector(),
            create_stub_reasoner(),
            {"deliver": lambda e: None},
            store=store,
            findings_store=fs,
        )
        assert env["success"] is True
        assert len(written) == 1  # write() was called exactly once

    def test_query_recent_findings_returns_stored(self):
        fs, _ = _fake_findings_store()
        fs["write"]("2026-07-30T00:00:00Z", "test-tenant", [
            {"key": "cap.throttle", "score": {"level": "Critical"}, "what": "Throttled", "where": "F64"},
        ])
        results = query_recent_findings(fs, scope="F64")
        assert len(results) == 1
        assert results[0]["findingKey"] == "cap.throttle"

    def test_format_context_produces_output(self):
        findings = [{"findingKey": "cap.throttle", "level": "Critical",
                     "whatText": "Throttled", "runAt": "2026-07-30T00:00:00Z"}]
        ctx = format_context(findings, scope="F64")
        assert "prior finding" in ctx.lower()
        assert "Throttled" in ctx

    def test_findings_store_write_failure_does_not_block_sweep(self):
        store, _ = _fake_store()

        def boom_write(run_at, tenant, findings):
            raise RuntimeError("write failed")

        fs = {"query": lambda **k: [], "write": boom_write}
        from fabric_audit_agent.reasoner_stub import create_stub_reasoner
        env = run_audit(
            _fake_collector(),
            create_stub_reasoner(),
            {"deliver": lambda e: None},
            store=store,
            findings_store=fs,
        )
        assert env["success"] is True


# ---------------------------------------------------------------------------
# Test 2: Outbound allowlist (all delivery refused)
# ---------------------------------------------------------------------------

def _sink():
    captured = []
    return captured, {"send": lambda payload: captured.append(payload)}


class TestOutboundPostDeliveryRemoval:
    def test_teams_notify_refused(self):
        captured, sink = _sink()
        out = dispatch_outbound("teams_notify", {"summary": "s"}, sinks={"teams": sink})
        assert out["dispatched"] is False
        assert captured == []

    def test_email_notify_refused(self):
        captured, sink = _sink()
        out = dispatch_outbound("email_notify", {"summary": "s"}, sinks={"email": sink})
        assert out["dispatched"] is False
        assert captured == []

    def test_ado_create_ticket_refused_disabled(self):
        captured, sink = _sink()
        out = dispatch_outbound("ado_create_ticket", {"summary": "s"}, sinks={"ticket": sink})
        assert out["dispatched"] is False
        assert captured == []

    def test_unknown_action_refused(self):
        captured, sink = _sink()
        out = dispatch_outbound("unknown_action", {"summary": "s"}, sinks={"x": sink})
        assert out["dispatched"] is False
        assert captured == []

    def test_allowlist_has_exactly_one_entry(self):
        from fabric_audit_agent.outbound import _ALLOWLIST
        assert len(_ALLOWLIST) == 1
        assert "ado_create_ticket" in _ALLOWLIST


# ---------------------------------------------------------------------------
# Test 3: Tier 2 returns empty delivered dict
# ---------------------------------------------------------------------------

class TestTier2EmptyDelivery:
    def test_delivered_always_empty(self):
        from fabric_audit_agent.automation.tier2_check import run_tier2_check
        collector = {"collect": lambda: {
            "capacity": {"baseCU": 64, "peakCuPct": 130, "throttled": True},
            "items": [], "models": [],
        }}
        result = run_tier2_check(collector)
        assert result["delivered"] == {}

    def test_delivered_empty_even_with_sinks(self):
        from fabric_audit_agent.automation.tier2_check import run_tier2_check
        captured, sink = _sink()
        collector = {"collect": lambda: {
            "capacity": {"baseCU": 64, "peakCuPct": 130, "throttled": True},
            "items": [], "models": [],
        }}
        result = run_tier2_check(collector, delivery_sinks={"teams": sink})
        assert result["delivered"] == {}
        assert captured == []
