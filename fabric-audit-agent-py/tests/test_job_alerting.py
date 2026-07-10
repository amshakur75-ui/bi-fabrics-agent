"""Phase 6 job wiring: alert-on-change in run_unified_job + email on the dead-man's-switch.

All offline — SMTP is exercised via a monkeypatched sender; email stays inert unless configured.
"""
from fabric_audit_agent import job as job_mod
from fabric_audit_agent.adapters import delivery_email as email_mod


def _env_smtp(**over):
    e = {"SMTP_HOST": "smtp.local", "SMTP_TO": "ops@x.com", "SMTP_FROM": "audit@x.com"}
    e.update(over)
    return e


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


# ---- _maybe_alert: fires only on material change, email inert unless configured ----
def test_maybe_alert_sends_on_material_change(monkeypatch):
    sent = []
    monkeypatch.setattr(email_mod, "_smtp_send", lambda msg, cfg: sent.append(msg))
    env = _env_smtp()
    envelope = _envelope([_cf("a", "Critical")])   # 'a' is new
    decision = job_mod._maybe_alert(envelope, prev_history=[_run([])], env=env)
    assert decision["alert"] is True
    assert len(sent) == 1


def test_maybe_alert_silent_on_no_change(monkeypatch):
    sent = []
    monkeypatch.setattr(email_mod, "_smtp_send", lambda msg, cfg: sent.append(msg))
    env = _env_smtp()
    envelope = _envelope([_cf("a", "Warning")])
    decision = job_mod._maybe_alert(envelope, prev_history=[_run([{"key": "a", "level": "Warning"}])], env=env)
    assert decision["alert"] is False
    assert sent == []


def test_maybe_alert_email_inert_when_unconfigured(monkeypatch):
    sent = []
    monkeypatch.setattr(email_mod, "_smtp_send", lambda msg, cfg: sent.append(msg))
    envelope = _envelope([_cf("a", "Critical")])
    decision = job_mod._maybe_alert(envelope, prev_history=[_run([])], env={})   # no SMTP
    assert decision["alert"] is True
    assert sent == []   # decided to alert, but nothing sent — prod default behaves as today


def test_maybe_alert_failure_isolated(monkeypatch):
    # An alert-path error must never propagate (would otherwise fail the sweep).
    import fabric_audit_agent.automation.alerting as alerting_mod
    monkeypatch.setattr(alerting_mod, "decide_alert",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert job_mod._maybe_alert(_envelope(), [], _env_smtp()) is None   # swallowed


# ---- run_unified_job: prev_history captured BEFORE the in-run append (snapshot contract) ----
def test_run_unified_job_captures_prev_history_before_append(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(email_mod, "_smtp_send", lambda msg, cfg: sent.append(msg))

    class _Store:
        def __init__(self, initial):
            self._runs = list(initial)
        def history(self):
            return list(self._runs)   # immutable snapshot — the load-bearing contract
        def append(self, run):
            self._runs.append(run)
            return len(self._runs)

    # Prior run had a Warning finding 'a'; this run's reasoner emits none -> 'a' is RESOLVED.
    store_obj = _Store([_run([{"key": "capacity.throttle::a", "level": "Warning", "suppressed": False}])])
    store = {"history": store_obj.history, "append": store_obj.append}

    cap = tmp_path / "data.csv"
    cap.write_text("Timepoint,Total CU Usage %,SKU\n2026-06-01T00:00:00,50,F64\n", encoding="utf-8")
    env = _env_smtp(FABRIC_CSV_PATHS=str(cap), AUDIT_HISTORY_PATH=str(tmp_path / "h.json"))

    envelope = job_mod.run_unified_job(
        env=env, out_dir=str(tmp_path / "out"),
        reasoner={"reason": lambda facts, flags: []},   # no findings this run
        delivery={"deliver": lambda e: None},
        store=store,
    )
    assert envelope["success"] is True
    # Resolved 'a' was detected only because prev_history was captured BEFORE run_audit appended
    # the current (finding-less) run — proving the pre-append snapshot.
    assert len(sent) == 1


def test_run_unified_job_alert_error_does_not_fail_sweep(tmp_path, monkeypatch):
    import fabric_audit_agent.automation.alerting as alerting_mod
    monkeypatch.setattr(alerting_mod, "decide_alert",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    cap = tmp_path / "data.csv"
    cap.write_text("Timepoint,Total CU Usage %,SKU\n2026-06-01T00:00:00,50,F64\n", encoding="utf-8")
    env = {"FABRIC_CSV_PATHS": str(cap), "AUDIT_HISTORY_PATH": str(tmp_path / "h.json")}
    envelope = job_mod.run_unified_job(env=env, out_dir=str(tmp_path / "out"),
                                       delivery={"deliver": lambda e: None})
    assert envelope["success"] is True   # sweep unaffected by the alert-path error


# ---- dead-man's-switch: email added alongside Teams, both inert unless configured ----
def test_alert_failure_emails_when_smtp_configured(monkeypatch):
    sent = []
    monkeypatch.setattr(email_mod, "_smtp_send", lambda msg, cfg: sent.append(msg))
    ok = job_mod._alert_failure(RuntimeError("boom"), _env_smtp(), now_iso="2026-07-09T00:00:00Z")
    assert ok is True
    assert len(sent) == 1
    assert "FAILED" in sent[0]["Subject"] or "FAILED" in sent[0].get_content()


def test_alert_failure_still_noop_false_when_nothing_configured():
    # No Teams webhook, no SMTP -> nothing sent, returns False (unchanged contract).
    assert job_mod._alert_failure(RuntimeError("x"), {}, now_iso="t") is False


def test_alert_failure_teams_and_email_both_fire(monkeypatch):
    posted = {}
    monkeypatch.setattr(job_mod, "_build_failure_delivery",
                        lambda env: {"deliver": lambda card: posted.update(card)})
    sent = []
    monkeypatch.setattr(email_mod, "_smtp_send", lambda msg, cfg: sent.append(msg))
    ok = job_mod._alert_failure(RuntimeError("boom"),
                                _env_smtp(TEAMS_WEBHOOK_URL="https://hook"), now_iso="t")
    assert ok is True
    assert posted and len(sent) == 1   # Teams AND email both delivered
