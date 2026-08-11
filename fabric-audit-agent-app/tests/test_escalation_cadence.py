"""Escalation must mean "unresolved for a long time", not "seen three times".

The rule was presence-based -- Warning -> Critical when a key appeared in the two most recent prior
runs -- which silently inherited the schedule. The Node original ran DAILY, so three runs meant three
days: a fair definition of "nobody has dealt with this". This deployment runs the sweep HOURLY, so
the identical code meant three HOURS, and a live sweep reported 37 of 41 findings as Critical.

No detector was wrong. The word "Critical" had stopped carrying information, and every surface
downstream reads it -- the digest's severity counts, the notification center's ordering, the ticket
glyphs. An inflated count trains the reader to ignore the label, which is worse than no label.
"""
from datetime import datetime, timedelta, timezone

import pytest

from fabric_audit_agent.automation.escalate import apply_escalation

NOW_DT = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)
NOW_MS = NOW_DT.timestamp() * 1000.0
KEY = "refresh.failure::Ent-Reporting-DTC"


def _run(hours_ago, keys=(KEY,)):
    t = NOW_DT - timedelta(hours=hours_ago)
    return {"runAt": t.isoformat().replace("+00:00", "Z"),
            "findings": [{"key": k} for k in keys]}


def _warning():
    return [{"key": KEY, "score": {"level": "Warning", "reason": "3 failures in 24h"}}]


def _level(history, findings=None, now_ms=NOW_MS):
    out = apply_escalation(findings or _warning(), history, now_ms=now_ms)
    return out[0]["score"]["level"]


def test_three_hourly_runs_is_not_a_crisis():
    """The live defect, stated as a test: on an hourly cron, three consecutive runs is three hours."""
    assert _level([_run(3), _run(2), _run(1)]) == "Warning"


def test_a_full_day_unresolved_does_escalate():
    assert _level([_run(h) for h in range(30, 0, -1)]) == "Critical"


def test_the_escalation_reason_states_the_elapsed_time():
    """An operator reading "escalated: unresolved 3 consecutive runs" cannot tell whether that means
    three hours or three days. The reason now says how long."""
    out = apply_escalation(_warning(), [_run(h) for h in range(30, 0, -1)], now_ms=NOW_MS)
    reason = out[0]["score"]["reason"]
    assert "unresolved for ~" in reason and "h" in reason
    assert "3 consecutive runs" not in reason


def test_a_daily_cadence_still_escalates_after_three_runs():
    """The original intent is preserved where it was already correct: three DAILY runs is three days,
    which is past the 24h floor."""
    assert _level([_run(72), _run(48), _run(24)]) == "Critical"


def test_an_intermittent_finding_is_measured_from_when_it_came_back():
    """"Unresolved for a day" has to mean a day of CONTINUOUS presence. Measuring from the first-ever
    sighting would let a finding that cleared and returned inherit the age of the old occurrence."""
    history = [_run(40), _run(39), _run(20, keys=()), _run(3), _run(2), _run(1)]
    assert _level(history) == "Warning"


def test_a_finding_absent_from_the_last_two_runs_never_escalates():
    """The anti-flapping guard is unchanged."""
    history = [_run(h) for h in range(40, 2, -1)] + [_run(2, keys=()), _run(1, keys=())]
    assert _level(history) == "Warning"


def test_the_time_floor_can_be_disabled(monkeypatch):
    """Escape hatch: restores the old presence-only rule without a code change."""
    monkeypatch.setenv("FABRIC_ESCALATE_AFTER_HOURS", "0")
    assert _level([_run(3), _run(2), _run(1)]) == "Critical"


def test_the_window_is_configurable(monkeypatch):
    monkeypatch.setenv("FABRIC_ESCALATE_AFTER_HOURS", "2")
    assert _level([_run(3), _run(2), _run(1)]) == "Critical"
    monkeypatch.setenv("FABRIC_ESCALATE_AFTER_HOURS", "100")
    assert _level([_run(h) for h in range(30, 0, -1)]) == "Warning"


def test_history_without_timestamps_keeps_the_old_behaviour():
    """Fail toward over-grading, not under-grading: if the age cannot be established, still escalate.
    A Critical that should be a Warning is a readability problem; a Warning that should be Critical
    hides something real."""
    history = [{"findings": [{"key": KEY}]}, {"findings": [{"key": KEY}]}]
    assert _level(history, now_ms=None) == "Critical"


def test_a_critical_finding_is_untouched_and_a_single_run_never_escalates():
    crit = [{"key": KEY, "score": {"level": "Critical", "reason": "already"}}]
    assert _level([_run(h) for h in range(30, 0, -1)], findings=crit) == "Critical"
    assert _level([_run(1)]) == "Warning"          # fewer than two prior runs


def test_the_pipeline_passes_the_real_clock_through():
    """Wired end to end: without now_ms the age is measured from the newest HISTORY run, which
    understates the current finding's age by one interval."""
    import inspect

    from fabric_audit_agent import pipeline
    src = inspect.getsource(pipeline.run_audit)
    assert "apply_escalation(findings, history, now_ms=" in src
