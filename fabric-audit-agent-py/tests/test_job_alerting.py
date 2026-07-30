"""Job wiring: alert-on-change decision + sweep isolation. Offline."""
from fabric_audit_agent import job as job_mod


def _envelope(findings=None, suppressed=None, verdict="optimize", sla=None):
    data = {"findings": findings or [], "verdict": {"decision": verdict, "reason": "r"}}
    if suppressed is not None:
        data["suppressed"] = suppressed
    if sla is not None:
        data["sla"] = sla
    return {"summary": "s", "data": data}


def _cf(key, level="Warning"):
    return {"key": key, "score": {"level": level, "reason": "r"}}


def _run(findings):
    return {"runAt": "t", "findings": findings, "verdictDecision": "optimize", "slaBreachedCount": 0}


def test_maybe_alert_returns_decision_on_material_change():
    envelope = _envelope([_cf("a", "Critical")])
    decision = job_mod._maybe_alert(envelope, prev_history=[_run([])], env={})
    assert decision["alert"] is True


def test_maybe_alert_silent_on_no_change():
    envelope = _envelope([_cf("a", "Warning")])
    decision = job_mod._maybe_alert(envelope, prev_history=[_run([{"key": "a", "level": "Warning"}])], env={})
    assert decision["alert"] is False


def test_maybe_alert_failure_isolated(monkeypatch):
    import fabric_audit_agent.automation.alerting as alerting_mod
    monkeypatch.setattr(alerting_mod, "decide_alert",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert job_mod._maybe_alert(_envelope(), [], env={}) is None


def test_alert_failure_is_noop():
    assert job_mod._alert_failure(RuntimeError("x"), {}, now_iso="t") is False


def test_run_unified_job_alert_error_does_not_fail_sweep(tmp_path, monkeypatch):
    import fabric_audit_agent.automation.alerting as alerting_mod
    monkeypatch.setattr(alerting_mod, "decide_alert",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    cap = tmp_path / "data.csv"
    cap.write_text("Timepoint,Total CU Usage %,SKU\n2026-06-01T00:00:00,50,F64\n", encoding="utf-8")
    env = {"FABRIC_CSV_PATHS": str(cap), "AUDIT_HISTORY_PATH": str(tmp_path / "h.json")}
    envelope = job_mod.run_unified_job(env=env, out_dir=str(tmp_path / "out"),
                                       delivery={"deliver": lambda e: None})
    assert envelope["success"] is True
