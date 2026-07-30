"""Tests for the Phase 9 Tier 2 deterministic check (tier2_check.py + job.py wiring).

All offline — no real network calls, no LLM calls, no Spark.
Tests cover: all 5 trigger conditions, the no-trigger case, recurrence detection,
delivery to Teams + email, failure isolation, heartbeat, and the job entry point.
"""
import json
import os
import pytest

from fabric_audit_agent.automation.tier2_check import (
    run_tier2_check,
    _check_concentration,
    _check_throttle,
    _check_pressure,
    _check_overage,
    _check_data_availability,
    _cross_reference_recurrence,
    _build_tier2_alert_summary,
)
from fabric_audit_agent.config import DEFAULT_CONFIG


# ---- helpers ----

def _fake_collector(facts):
    """A collector port returning the given facts dict."""
    return {"collect": lambda: facts}


def _failing_collector():
    """A collector port that raises."""
    def _boom():
        raise RuntimeError("collector exploded")
    return {"collect": _boom}


def _capturing_sink():
    """Returns (captured_list, sink_dict) for tracking deliveries."""
    captured = []
    return captured, {"deliver": lambda e: captured.append(e)}


def _facts(capacity=None, items=None):
    """Build a facts dict."""
    f = {}
    if capacity is not None:
        f["capacity"] = capacity
    if items is not None:
        f["items"] = items
    return f


# ===========================================================================
# 1. concentration_gate triggers
# ===========================================================================

class TestCheckConcentration:
    def test_triggers_on_high_share(self):
        items = [{"name": "BigReport", "sharePct": 45.0, "workspace": "ws1"}]
        triggers = _check_concentration({"items": items})
        assert len(triggers) == 1
        assert triggers[0]["check"] == "concentration"
        assert triggers[0]["sharePct"] == 45.0
        assert triggers[0]["gate"]["passed"] is True

    def test_no_trigger_below_threshold(self):
        items = [{"name": "SmallReport", "sharePct": 10.0}]
        triggers = _check_concentration({"items": items})
        assert triggers == []

    def test_custom_threshold_from_config(self):
        items = [{"name": "Report", "sharePct": 25.0}]
        config = {"capacity": {"concentrationPct": 20}}
        triggers = _check_concentration({"items": items}, config=config)
        assert len(triggers) == 1

    def test_no_items_no_trigger(self):
        assert _check_concentration({}) == []
        assert _check_concentration(None) == []

    def test_invalid_sharePct_skipped(self):
        items = [{"name": "Bad", "sharePct": "not-a-number"}]
        triggers = _check_concentration({"items": items})
        assert triggers == []

    def test_multiple_items_above_threshold(self):
        items = [
            {"name": "A", "sharePct": 35.0, "owner": "alice"},
            {"name": "B", "sharePct": 40.0, "owner": "bob"},
        ]
        triggers = _check_concentration({"items": items})
        assert len(triggers) == 2


# ===========================================================================
# 2. throttle_claim_gate triggers
# ===========================================================================

class TestCheckThrottle:
    def test_triggers_on_throttle_minutes(self):
        cap = {"throttleMinutes": 5.0, "peakCuPct": 120.0}
        triggers = _check_throttle({"capacity": cap})
        assert len(triggers) == 1
        assert triggers[0]["check"] == "throttle"
        assert triggers[0]["gate"]["passed"] is True
        assert triggers[0]["throttleMinutes"] == 5.0

    def test_no_trigger_zero_throttle(self):
        cap = {"throttleMinutes": 0, "peakCuPct": 80.0}
        triggers = _check_throttle({"capacity": cap})
        assert triggers == []

    def test_no_trigger_high_cu_without_throttle(self):
        """High CU alone does NOT constitute throttling (smoothing absorbs bursts)."""
        cap = {"peakCuPct": 150.0}
        triggers = _check_throttle({"capacity": cap})
        assert triggers == []

    def test_no_capacity_data(self):
        assert _check_throttle({}) == []


# ===========================================================================
# 3. pressure_claim_gate triggers (CU > 100% without throttle signal)
# ===========================================================================

class TestCheckPressure:
    def test_triggers_on_high_cu(self):
        cap = {"peakCuPct": 110.0}
        triggers = _check_pressure({"capacity": cap})
        assert len(triggers) == 1
        assert triggers[0]["check"] == "pressure"

    def test_no_trigger_normal_cu(self):
        cap = {"peakCuPct": 85.0}
        triggers = _check_pressure({"capacity": cap})
        assert triggers == []

    def test_no_capacity_data(self):
        assert _check_pressure({}) == []


# ===========================================================================
# 4. overage check
# ===========================================================================

class TestCheckOverage:
    def test_triggers_on_nonzero_overage(self):
        cap = {"overageTotalMs": 15000.0, "overageCumulativePct": 2.5, "minutesToBurndown": 0.5}
        triggers = _check_overage({"capacity": cap})
        assert len(triggers) == 1
        assert triggers[0]["check"] == "overage"
        assert triggers[0]["overageTotalMs"] == 15000.0
        assert triggers[0]["minutesToBurndown"] == 0.5

    def test_no_trigger_zero_overage(self):
        cap = {"overageTotalMs": 0}
        triggers = _check_overage({"capacity": cap})
        assert triggers == []

    def test_no_trigger_missing_overage(self):
        cap = {"peakCuPct": 80}
        triggers = _check_overage({"capacity": cap})
        assert triggers == []


# ===========================================================================
# 5. null data gate (data availability)
# ===========================================================================

class TestCheckDataAvailability:
    def test_triggers_on_none(self):
        triggers = _check_data_availability(None)
        assert len(triggers) == 1
        assert triggers[0]["check"] == "data_unavailable"

    def test_triggers_on_empty_dict(self):
        triggers = _check_data_availability({})
        assert len(triggers) == 1

    def test_no_trigger_on_valid_data(self):
        facts = {"capacity": {"peakCuPct": 50}}
        triggers = _check_data_availability(facts)
        assert triggers == []

    def test_triggers_on_error(self):
        facts = {"error": "connection refused"}
        triggers = _check_data_availability(facts)
        assert len(triggers) == 1


# ===========================================================================
# No-trigger case
# ===========================================================================

class TestNoTrigger:
    def test_healthy_capacity_no_triggers(self):
        facts = _facts(
            capacity={"peakCuPct": 60.0, "throttleMinutes": 0, "overageTotalMs": 0},
            items=[{"name": "Report1", "sharePct": 10.0}],
        )
        result = run_tier2_check(_fake_collector(facts))
        assert result["triggered"] is False
        assert result["triggers"] == []

    def test_all_below_threshold(self):
        facts = _facts(
            capacity={"peakCuPct": 95.0, "throttleMinutes": 0},
            items=[
                {"name": "A", "sharePct": 15.0},
                {"name": "B", "sharePct": 12.0},
            ],
        )
        result = run_tier2_check(_fake_collector(facts))
        assert result["triggered"] is False


# ===========================================================================
# Recurrence detection
# ===========================================================================

class TestRecurrenceDetection:
    def test_marks_recurring_when_prior_finding_matches(self):
        triggers = [{"check": "concentration", "sharePct": 45.0}]
        store = {"query": lambda **kw: [
            {"findingKey": "capacity.concentration::ws/BigReport", "level": "Warning",
             "whatText": "high", "runAt": "2026-07-29"},
        ]}
        result = _cross_reference_recurrence(triggers, store)
        assert result[0]["recurrence"]["isRecurring"] is True
        assert len(result[0]["recurrence"]["matchingFindings"]) == 1

    def test_marks_not_recurring_when_no_match(self):
        triggers = [{"check": "throttle", "throttleMinutes": 5.0}]
        store = {"query": lambda **kw: [
            {"findingKey": "capacity.concentration::ws/X", "level": "Warning",
             "whatText": "conc", "runAt": "2026-07-29"},
        ]}
        result = _cross_reference_recurrence(triggers, store)
        assert result[0]["recurrence"]["isRecurring"] is False

    def test_no_store_returns_triggers_unchanged(self):
        triggers = [{"check": "concentration"}]
        result = _cross_reference_recurrence(triggers, None)
        assert result == triggers
        assert "recurrence" not in result[0]

    def test_store_error_does_not_crash(self):
        triggers = [{"check": "concentration"}]
        store = {"query": lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))}
        def bad_query(**kw):
            raise RuntimeError("boom")
        store = {"query": bad_query}
        result = _cross_reference_recurrence(triggers, store)
        assert result == triggers  # no crash, triggers returned as-is

    def test_empty_triggers_short_circuits(self):
        store = {"query": lambda **kw: [{"findingKey": "k"}]}
        result = _cross_reference_recurrence([], store)
        assert result == []


# ===========================================================================
# run_tier2_check integration
# ===========================================================================

class TestRunTier2Check:
    def test_concentration_triggers_alert(self):
        facts = _facts(
            capacity={"peakCuPct": 50.0, "throttleMinutes": 0},
            items=[{"name": "HotReport", "sharePct": 55.0, "workspace": "ws1"}],
        )
        result = run_tier2_check(_fake_collector(facts))
        assert result["triggered"] is True
        assert any(t["check"] == "concentration" for t in result["triggers"])

    def test_throttle_triggers_alert(self):
        facts = _facts(
            capacity={"peakCuPct": 120.0, "throttleMinutes": 10.0},
            items=[],
        )
        result = run_tier2_check(_fake_collector(facts))
        assert result["triggered"] is True
        assert any(t["check"] == "throttle" for t in result["triggers"])

    def test_pressure_triggers_alert(self):
        facts = _facts(
            capacity={"peakCuPct": 110.0, "throttleMinutes": 0},
            items=[],
        )
        result = run_tier2_check(_fake_collector(facts))
        assert result["triggered"] is True
        assert any(t["check"] == "pressure" for t in result["triggers"])

    def test_overage_triggers_alert(self):
        facts = _facts(
            capacity={"overageTotalMs": 5000.0},
            items=[],
        )
        result = run_tier2_check(_fake_collector(facts))
        assert result["triggered"] is True
        assert any(t["check"] == "overage" for t in result["triggers"])

    def test_data_unavailable_does_not_trigger(self):
        """The data_unavailable check is a STOP gate — inconclusive, not an alert trigger."""
        result = run_tier2_check(_fake_collector(None))
        assert result["triggered"] is False
        assert any(t["check"] == "data_unavailable" for t in result["triggers"])

    def test_collector_failure_returns_error(self):
        result = run_tier2_check(_failing_collector())
        assert result["triggered"] is False
        assert result.get("error") == "collector failed"

    def test_multiple_triggers_all_reported(self):
        facts = _facts(
            capacity={"peakCuPct": 130.0, "throttleMinutes": 5.0, "overageTotalMs": 8000.0},
            items=[{"name": "HotItem", "sharePct": 60.0}],
        )
        result = run_tier2_check(_fake_collector(facts))
        assert result["triggered"] is True
        checks = {t["check"] for t in result["triggers"]}
        assert "concentration" in checks
        assert "throttle" in checks
        assert "pressure" in checks
        assert "overage" in checks

    def test_delivers_to_teams_and_email(self):
        teams_captured, teams_sink = _capturing_sink()
        email_captured, email_sink = _capturing_sink()
        facts = _facts(
            capacity={"peakCuPct": 120.0, "throttleMinutes": 5.0},
            items=[],
        )
        result = run_tier2_check(
            _fake_collector(facts),
            delivery_sinks={"teams": teams_sink, "email": email_sink},
        )
        assert result["triggered"] is True
        assert result["delivered"].get("teams") is True
        assert result["delivered"].get("email") is True
        assert len(teams_captured) == 1
        assert len(email_captured) == 1

    def test_no_delivery_when_no_trigger(self):
        teams_captured, teams_sink = _capturing_sink()
        facts = _facts(
            capacity={"peakCuPct": 50.0, "throttleMinutes": 0},
            items=[],
        )
        result = run_tier2_check(
            _fake_collector(facts),
            delivery_sinks={"teams": teams_sink},
        )
        assert result["triggered"] is False
        assert teams_captured == []

    def test_delivery_failure_isolated(self):
        """A broken sink must never prevent the check from completing."""
        def boom(payload):
            raise RuntimeError("Teams is down")
        facts = _facts(capacity={"peakCuPct": 120.0, "throttleMinutes": 5.0}, items=[])
        result = run_tier2_check(
            _fake_collector(facts),
            delivery_sinks={"teams": {"deliver": boom}},
        )
        assert result["triggered"] is True
        assert result["delivered"].get("teams") is False

    def test_heartbeat_written_on_every_run(self):
        written = []
        hb_store = {"write": lambda ts: written.append(ts)}
        facts = _facts(capacity={"peakCuPct": 50}, items=[])
        result = run_tier2_check(_fake_collector(facts), heartbeat_store=hb_store)
        assert len(written) == 1
        assert result["checkedAt"] == written[0]

    def test_heartbeat_failure_does_not_block_check(self):
        def bad_write(ts):
            raise RuntimeError("disk full")
        hb_store = {"write": bad_write}
        facts = _facts(capacity={"peakCuPct": 120, "throttleMinutes": 3}, items=[])
        result = run_tier2_check(_fake_collector(facts), heartbeat_store=hb_store)
        assert result["triggered"] is True  # check still runs despite heartbeat failure


# ===========================================================================
# Alert summary building
# ===========================================================================

class TestBuildAlertSummary:
    def test_concentration_summary(self):
        triggers = [{"check": "concentration", "item": "BigReport", "sharePct": 45.0}]
        s = _build_tier2_alert_summary(triggers)
        assert "Concentration" in s
        assert "BigReport" in s

    def test_throttle_summary(self):
        triggers = [{"check": "throttle", "throttleMinutes": 10.0}]
        s = _build_tier2_alert_summary(triggers)
        assert "Throttling" in s

    def test_multiple_triggers_summary(self):
        triggers = [
            {"check": "concentration", "item": "X", "sharePct": 50.0},
            {"check": "throttle", "throttleMinutes": 5.0},
        ]
        s = _build_tier2_alert_summary(triggers)
        assert "Concentration" in s
        assert "Throttling" in s

    def test_recurrence_noted(self):
        triggers = [
            {"check": "concentration", "item": "X", "sharePct": 50.0,
             "recurrence": {"isRecurring": True}},
        ]
        s = _build_tier2_alert_summary(triggers)
        assert "recurring" in s.lower()

    def test_empty_triggers(self):
        s = _build_tier2_alert_summary([])
        assert "no triggers" in s.lower()


# ===========================================================================
# Job wiring (job.py tier2 entry points)
# ===========================================================================

class TestJobTier2Wiring:
    def test_run_tier2_job_with_injected_ports(self):
        from fabric_audit_agent import job as job_mod
        facts = _facts(
            capacity={"peakCuPct": 120.0, "throttleMinutes": 3.0},
            items=[],
        )
        result = job_mod.run_tier2_job(
            env={},
            collector=_fake_collector(facts),
            delivery_sinks={},
            heartbeat_store=None,
        )
        assert result["triggered"] is True

    def test_run_tier2_job_no_trigger(self):
        from fabric_audit_agent import job as job_mod
        facts = _facts(capacity={"peakCuPct": 50.0}, items=[])
        result = job_mod.run_tier2_job(
            env={},
            collector=_fake_collector(facts),
            delivery_sinks={},
        )
        assert result["triggered"] is False

    def test_heartbeat_store_write_and_read(self, tmp_path):
        from fabric_audit_agent import job as job_mod
        hb_path = str(tmp_path / "heartbeat.json")
        env = {"FABRIC_TIER2_HEARTBEAT_PATH": hb_path}
        store = job_mod._tier2_heartbeat_store(env)
        assert store is not None
        store["write"]("2026-07-29T12:00:00Z")
        state = store["read"]()
        assert state["lastRun"] == "2026-07-29T12:00:00Z"
        assert state["tier"] == "tier2"

    def test_heartbeat_store_none_when_no_path(self):
        from fabric_audit_agent import job as job_mod
        store = job_mod._tier2_heartbeat_store({})
        assert store is None

    def test_check_tier2_heartbeat_stale(self):
        from fabric_audit_agent import job as job_mod
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"lastRun": "2026-01-01T00:00:00Z", "tier": "tier2"}, f)
            path = f.name
        try:
            env = {"FABRIC_TIER2_HEARTBEAT_PATH": path}
            result = job_mod._check_tier2_heartbeat(env)
            assert result["checked"] is True
            assert result["stale"] is True
            assert result["ageMinutes"] > 60
        finally:
            os.unlink(path)

    def test_check_tier2_heartbeat_fresh(self, tmp_path):
        from fabric_audit_agent import job as job_mod
        from datetime import datetime, timezone
        hb_path = str(tmp_path / "heartbeat.json")
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with open(hb_path, "w") as f:
            json.dump({"lastRun": now_iso, "tier": "tier2"}, f)
        env = {"FABRIC_TIER2_HEARTBEAT_PATH": hb_path}
        result = job_mod._check_tier2_heartbeat(env)
        assert result["checked"] is True
        assert result["stale"] is False

    def test_check_tier2_heartbeat_no_file(self, tmp_path):
        from fabric_audit_agent import job as job_mod
        env = {"FABRIC_TIER2_HEARTBEAT_PATH": str(tmp_path / "missing.json")}
        result = job_mod._check_tier2_heartbeat(env)
        assert result["checked"] is True
        assert result["stale"] is True

    def test_check_tier2_heartbeat_no_path(self):
        from fabric_audit_agent import job as job_mod
        result = job_mod._check_tier2_heartbeat({})
        assert result["checked"] is False


# ===========================================================================
# _maybe_alert sends to BOTH Teams and email (Phase 9)
# ===========================================================================

class TestMaybeAlertTeamsAndEmail:
    def test_both_channels_fire_on_material_change(self, monkeypatch):
        from fabric_audit_agent import job as job_mod
        from fabric_audit_agent.adapters import delivery_email as email_mod

        email_sent = []
        monkeypatch.setattr(email_mod, "_smtp_send", lambda msg, cfg: email_sent.append(msg))

        teams_posted = []
        original_build = job_mod._build_failure_delivery
        # We can't monkeypatch PlainJsonHttp easily, so monkeypatch at dispatch_outbound level
        from fabric_audit_agent import outbound as outbound_mod
        original_dispatch = outbound_mod.dispatch_outbound
        def tracking_dispatch(action_type, payload, *, sinks):
            if action_type == "teams_notify":
                for sink_name, sink in sinks.items():
                    teams_posted.append(payload)
                    return {"dispatched": True, "delivered": True, "actionType": action_type,
                            "disclosure": None, "reason": None}
            return original_dispatch(action_type, payload, sinks=sinks)
        monkeypatch.setattr(outbound_mod, "dispatch_outbound", tracking_dispatch)

        env = {"SMTP_HOST": "smtp.local", "SMTP_TO": "ops@x.com",
               "TEAMS_WEBHOOK_URL": "https://logic.azure.com/test"}
        envelope = {"summary": "test", "data": {"findings": [
            {"key": "new-finding", "score": {"level": "Critical", "reason": "r"}}
        ], "verdict": {"decision": "optimize", "reason": "r"}}}
        prev = [{"runAt": "t", "findings": [], "verdictDecision": "optimize", "slaBreachedCount": 0}]

        decision = job_mod._maybe_alert(envelope, prev, env)
        assert decision["alert"] is True
        assert len(teams_posted) == 1
        assert len(email_sent) == 1

    def test_alert_path_error_does_not_fail(self, monkeypatch):
        from fabric_audit_agent import job as job_mod
        import fabric_audit_agent.automation.alerting as alerting_mod
        monkeypatch.setattr(alerting_mod, "decide_alert",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        env = {"TEAMS_WEBHOOK_URL": "https://hook"}
        result = job_mod._maybe_alert({"summary": "s", "data": {"findings": []}}, [], env)
        assert result is None  # swallowed, sweep continues


# ===========================================================================
# _alert_failure sends to both channels
# ===========================================================================

class TestAlertFailureBothChannels:
    def test_alert_failure_teams_and_email(self, monkeypatch):
        from fabric_audit_agent import job as job_mod
        from fabric_audit_agent.adapters import delivery_email as email_mod

        teams_posted = {}
        monkeypatch.setattr(job_mod, "_build_failure_delivery",
                            lambda env: {"deliver": lambda card: teams_posted.update(card)})
        email_sent = []
        monkeypatch.setattr(email_mod, "_smtp_send", lambda msg, cfg: email_sent.append(msg))

        env = {"SMTP_HOST": "smtp.local", "SMTP_TO": "ops@x.com",
               "TEAMS_WEBHOOK_URL": "https://hook"}
        ok = job_mod._alert_failure(RuntimeError("boom"), env, now_iso="t")
        assert ok is True
        assert teams_posted  # Teams got the card
        assert len(email_sent) == 1  # Email also got it


# ===========================================================================
# Outbound allowlist: teams_notify is now enabled
# ===========================================================================

class TestTeamsNotifyEnabled:
    def test_teams_notify_dispatches_when_enabled(self):
        from fabric_audit_agent.outbound import dispatch_outbound
        captured, sink = _capturing_sink()
        payload = {"summary": "test alert", "data": {"findings": []}}
        out = dispatch_outbound("teams_notify", payload, sinks={"teams": sink})
        assert out["dispatched"] is True
        assert out["delivered"] is True
        assert len(captured) == 1

    def test_ado_still_disabled(self):
        from fabric_audit_agent.outbound import dispatch_outbound
        captured, sink = _capturing_sink()
        out = dispatch_outbound("ado_create_ticket", {"summary": "s"}, sinks={"ticket": sink})
        assert out["dispatched"] is False
        assert captured == []
