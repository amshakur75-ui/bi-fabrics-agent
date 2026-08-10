"""Dead-man's switch: what actually makes a dead agent noticeable, stated honestly.

This file used to be titled "a crashed sweep must alert and ALWAYS re-raise" while its first two
tests asserted ``_alert_failure(...) is False`` — i.e. it PROVED the alerting was a no-op under a
docstring claiming it worked. That is false confidence, the most dangerous kind of test.

The honest account of the mechanism, as shipped:

  * ``_alert_failure`` IS a stub and is not the alerting path. The real path is the re-raise: every
    ``*_main`` entrypoint re-raises, Databricks marks the run FAILED, and each job stanza's
    ``email_notifications.on_failure`` fires. So the re-raise is the load-bearing behaviour and is
    what these tests pin.
  * A *crash* is therefore covered. What was NOT covered is the tier2 job being paused, unscheduled
    or hung — no crash, so nothing to re-raise. That is what ``_check_tier2_health`` is for, and its
    entire response body used to be ``pass``: the detector was correct and had five tests while the
    response was untested because there wasn't one. It now records a health issue, which reaches the
    sweep's Degraded line and (via tier2_main's raise-on-degraded) an actual email.
"""
import pytest

from fabric_audit_agent import job as job_mod
from fabric_audit_agent.automation.health import HealthReport


# ---- _alert_failure: a stub, and labelled as one -------------------------------

def test_alert_failure_is_still_a_stub_not_the_alerting_path():
    """Pinned as a STUB deliberately. If someone implements it, this test should fail and be
    rewritten to assert what it delivers — that is the point of pinning it."""
    assert job_mod._alert_failure(RuntimeError("x"), {}, now_iso="t") is False
    assert job_mod._alert_failure(RuntimeError("x"), {"SOME_KEY": "v"}, now_iso="t") is False


# ---- the re-raise is the mechanism --------------------------------------------

def test_job_main_reraises_so_databricks_marks_the_run_failed(monkeypatch):
    """The re-raise -- not _alert_failure -- is what makes on_failure email fire."""
    monkeypatch.setattr(job_mod, "run_unified_job",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("dead")))
    with pytest.raises(RuntimeError, match="dead"):
        job_mod.job_main()


def test_legacy_main_also_reraises(monkeypatch):
    monkeypatch.setattr(job_mod, "run_job", lambda: (_ for _ in ()).throw(RuntimeError("dead2")))
    with pytest.raises(RuntimeError, match="dead2"):
        job_mod.main()


# ---- the actual dead-man case: no crash, just silence -------------------------

def test_a_stale_tier2_heartbeat_is_recorded_not_discarded(monkeypatch):
    """The gap a crash-only switch leaves: if tier2 is paused, de-scheduled or hangs, there is no
    exception to re-raise and every surface stays green. The response to `stale` used to be
    literally `pass`, so the one detector that could see this threw its answer away."""
    monkeypatch.setattr(job_mod, "_check_tier2_heartbeat",
                        lambda env: {"stale": True, "ageMinutes": 330, "thresholdMinutes": 15})
    health = HealthReport()
    job_mod._check_tier2_health({}, health=health)
    assert health.degraded is True
    assert "heartbeat STALE" in health.summary
    assert "330" in health.summary          # the operator needs the age, not just the fact


def test_a_fresh_tier2_heartbeat_records_nothing():
    """No false positives: a healthy heartbeat must not degrade the sweep."""
    health = HealthReport()
    job_mod._check_tier2_health({}, health=health)   # no heartbeat path configured -> not stale
    assert health.degraded is False


def test_a_failing_heartbeat_check_is_itself_recorded(monkeypatch):
    """"We could not tell whether tier2 is alive" is a degraded state, not a healthy one."""
    def boom(env):
        raise RuntimeError("volume unreachable")
    monkeypatch.setattr(job_mod, "_check_tier2_heartbeat", boom)
    health = HealthReport()
    job_mod._check_tier2_health({}, health=health)
    assert health.degraded is True
    assert "volume unreachable" in health.summary


def test_the_heartbeat_check_never_fails_the_sweep(monkeypatch):
    """Failure-isolated: the sweep's own findings are worth more than this check."""
    def boom(env):
        raise RuntimeError("boom")
    monkeypatch.setattr(job_mod, "_check_tier2_heartbeat", boom)
    assert job_mod._check_tier2_health({}, health=None) is None    # no raise, no health needed
