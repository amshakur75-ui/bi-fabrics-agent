"""Dead-man's-switch: a crashed sweep must alert (when a webhook exists) and ALWAYS re-raise."""
import pytest
from fabric_audit_agent import job as job_mod


def test_alert_failure_posts_card_and_reports_true(monkeypatch):
    posted = {}
    monkeypatch.setattr(job_mod, "_build_failure_delivery",
                        lambda env: {"deliver": lambda card: posted.update(card)})
    ok = job_mod._alert_failure(RuntimeError("secret expired"),
                                {"TEAMS_WEBHOOK_URL": "https://hook"}, now_iso="2026-07-07T12:00:00Z")
    assert ok is True
    assert "FAILED" in str(posted) and "secret expired" in str(posted)

def test_alert_failure_without_webhook_is_noop_false():
    assert job_mod._alert_failure(RuntimeError("x"), {}, now_iso="t") is False

def test_alert_failure_swallows_delivery_errors(monkeypatch):
    def boom(env):
        raise OSError("teams down")
    monkeypatch.setattr(job_mod, "_build_failure_delivery", boom)
    assert job_mod._alert_failure(RuntimeError("x"), {"TEAMS_WEBHOOK_URL": "h"}, now_iso="t") is False

def test_job_main_alerts_then_reraises(monkeypatch):
    # job_main is the DEPLOYED entrypoint (pyproject: fabric-audit-job = job:job_main).
    calls = {}
    monkeypatch.setattr(job_mod, "run_unified_job",
                        lambda: (_ for _ in ()).throw(RuntimeError("dead")))
    monkeypatch.setattr(job_mod, "_alert_failure",
                        lambda exc, env, now_iso=None: calls.setdefault("alerted", str(exc)))
    with pytest.raises(RuntimeError, match="dead"):
        job_mod.job_main()
    assert calls["alerted"] == "dead"

def test_legacy_main_also_guarded(monkeypatch):
    calls = {}
    monkeypatch.setattr(job_mod, "run_job", lambda: (_ for _ in ()).throw(RuntimeError("dead2")))
    monkeypatch.setattr(job_mod, "_alert_failure",
                        lambda exc, env, now_iso=None: calls.setdefault("alerted", str(exc)))
    with pytest.raises(RuntimeError, match="dead2"):
        job_mod.main()
    assert calls["alerted"] == "dead2"

def test_alert_card_summary_carries_error_text(monkeypatch):
    # build_teams_card only reads envelope["summary"]/["data"] — the error text MUST live in
    # summary or the production card silently loses the diagnostic payload.
    posted = {}
    monkeypatch.setattr(job_mod, "_build_failure_delivery",
                        lambda env: {"deliver": lambda card: posted.update(card)})
    job_mod._alert_failure(RuntimeError("secret expired"),
                           {"TEAMS_WEBHOOK_URL": "h"}, now_iso="2026-07-07T12:00:00Z")
    assert "RuntimeError" in posted["summary"] and "secret expired" in posted["summary"]
