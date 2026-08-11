"""A detector that stops running must reach a human, not just a ticket twice a day.

`detect_all` is failure-isolated on purpose: one broken detector must not cost the whole sweep. But
the isolation was TOTAL. A crashed detector emitted a `meta.detector-error` flag and nothing else --
the run reported TERMINATED SUCCESS, so `email_notifications.on_failure` could not fire, and the only
trace was a ticket in a digest sent twice a day.

That is the worst thing to hide. When a detector stops running its findings are simply ABSENT, so
every surface still looks healthy: no alert, no ticket, no anomaly — just quietly reduced coverage.
It is the same class of problem as `silent_failure` (the collector-blind alarm), which is already
treated as Teams-worthy on the grounds that the failure mode must not hide the alarm for the failure
mode.

One of the three isolation sites — `cross_workspace_patterns` — was a bare `except Exception: pass`
and left no trace at all, not even a flag.
"""
import pytest

from fabric_audit_agent.automation.health import HealthReport
from fabric_audit_agent.detectors import detect_all

FACTS = {"capacity": {"peakCuPct": 40.0}, "items": []}


def _boom(facts, config):
    raise RuntimeError("kusto column renamed")


def test_a_crashed_detector_degrades_the_run():
    health = HealthReport()
    detect_all(FACTS, None, [_boom], health=health)
    assert health.degraded is True, "a detector that stopped running must not report SUCCESS"
    assert "boom" in health.summary and "FAILED" in health.summary
    assert "kusto column renamed" in health.summary, "the operator needs the cause, not just the fact"


def test_the_crash_is_still_isolated():
    """The fix must not turn one broken detector into a failed sweep mid-run: the OTHER detectors
    still have to run, and the flag is still emitted."""
    calls = []

    def _ok(facts, config):
        calls.append(1)
        return [{"type": "capacity.throttle", "resource": "capacity", "evidence": {}}]

    health = HealthReport()
    flags = detect_all(FACTS, None, [_boom, _ok], health=health)
    assert calls == [1], "a detector after the broken one must still run"
    assert any(f["type"] == "meta.detector-error" for f in flags)
    assert any(f["type"] == "capacity.throttle" for f in flags)


def test_a_healthy_sweep_is_not_degraded():
    health = HealthReport()
    detect_all(FACTS, None, [lambda f, c: []], health=health)
    assert health.degraded is False


def test_omitting_health_preserves_the_old_behaviour():
    """Every other caller (tests, CLI, MCP) passes no health and must be unaffected."""
    flags = detect_all(FACTS, None, [_boom])
    assert [f["type"] for f in flags] == ["meta.detector-error"]


def test_health_accounting_never_breaks_a_sweep():
    """Defence in depth: a broken HealthReport must not convert an isolated detector failure into a
    fatal one. The point of this code is to make failures visible, not to add a new failure mode."""
    class _Hostile:
        degraded = False
        summary = ""

        def record_issue(self, msg):
            raise RuntimeError("health store exploded")

    flags = detect_all(FACTS, None, [_boom], health=_Hostile())
    assert [f["type"] for f in flags] == ["meta.detector-error"]


def test_the_cross_workspace_failure_is_no_longer_silent(monkeypatch):
    """This site was `except Exception: pass` — the only one that left NO trace whatsoever. The whole
    `pattern.cross-workspace` family could stop being produced and nothing anywhere would say so."""
    import fabric_audit_agent.detectors.cross_workspace as cw

    def _explode(flags, min_workspaces=3):
        raise RuntimeError("clustering blew up")

    monkeypatch.setattr(cw, "cross_workspace_patterns", _explode)
    health = HealthReport()
    flags = detect_all(FACTS, None, [lambda f, c: []], health=health)
    assert health.degraded is True
    assert "cross_workspace_patterns" in health.summary
    assert any(f["type"] == "meta.detector-error"
               and f["resource"] == "cross_workspace_patterns" for f in flags), \
        "the failure must also leave a ticket, not only a health line"


def test_the_pipeline_and_job_thread_health_all_the_way_down():
    """The chain is only worth anything end to end: job_main builds the HealthReport, run_unified_job
    passes it to run_audit, run_audit passes it to detect_all, and job_main raises on degraded — which
    is what actually sends the email."""
    import inspect

    from fabric_audit_agent import job as job_mod
    from fabric_audit_agent import pipeline

    assert "detect_all(facts, config, health=health)" in inspect.getsource(pipeline.run_audit)
    unified = inspect.getsource(job_mod.run_unified_job)
    assert "health=health" in unified, "run_unified_job must forward its HealthReport to run_audit"
    assert "health.degraded" in inspect.getsource(job_mod.job_main)


def test_a_degraded_detector_actually_fails_the_job(monkeypatch):
    """The behavioural end of that chain."""
    from fabric_audit_agent import job as job_mod

    monkeypatch.setattr(job_mod, "_check_startup_invariant", lambda health: None)
    monkeypatch.setattr(job_mod, "_run_startup_preflight", lambda env, health: None)

    def fake_run(**kw):
        kw["health"].record_issue("detector detect_refresh FAILED and was skipped: KeyError: 'ts'")
        return {"summary": "sweep ok"}

    monkeypatch.setattr(job_mod, "run_unified_job", fake_run)
    with pytest.raises(RuntimeError, match="degraded"):
        job_mod.job_main()
