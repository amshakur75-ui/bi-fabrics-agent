"""Dead-man's-switch: a crashed sweep must alert and ALWAYS re-raise.
Delivery is stubbed (Phase 10 provides real delivery); verify the entrypoint guard still works."""
import pytest
from fabric_audit_agent import job as job_mod


def test_alert_failure_is_noop():
    assert job_mod._alert_failure(RuntimeError("x"), {}, now_iso="t") is False


def test_alert_failure_with_any_env_still_noop():
    assert job_mod._alert_failure(RuntimeError("x"), {"SOME_KEY": "v"}, now_iso="t") is False


def test_job_main_alerts_then_reraises(monkeypatch):
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
