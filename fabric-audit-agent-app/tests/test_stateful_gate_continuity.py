"""Stateful gates must not assert a DURATION that never happened.

`_check_sustained_band` and `_check_rate_of_change` derive a human-facing time span from the
NUMBER of readings, assuming every reading is one 5-minute sweep apart. Any paused / skipped /
failed run, deploy gap, or readings-store outage breaks that assumption.

This is not hypothetical: during the real 2026-08-09/10 outage every job was dead for 5.5 hours.
On recovery the two adjacent readings either side of the gap would have produced
"CU% climbed 20 points in 5 minutes" for a 5h30m drift, and a "20 minutes in band" claim spanning
days. A ticket stating a duration that never occurred is worse than no ticket — it sends a human
looking for a spike that did not exist.
"""
from datetime import datetime, timedelta, timezone

from fabric_audit_agent.automation.tier2_check import (
    _check_sustained_band, _check_rate_of_change, _readings_contiguous,
)
from fabric_audit_agent.automation.materiality import load_cfg

T0 = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _r(minutes_ago, peak):
    """One readings-store row, newest-first ordering handled by the caller."""
    return {"runAt": (T0 - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z"),
            "peakCuPct": peak, "throttleMinutes": 0.0, "itemCount": 1, "collectorOk": True}


def _contiguous(peaks):
    """Readings 5 minutes apart, newest first."""
    return [_r(i * 5, p) for i, p in enumerate(peaks)]


# --- rate of change ---------------------------------------------------------------------

def test_rate_change_fires_on_two_adjacent_sweeps():
    trigs = _check_rate_of_change(_contiguous([88.0, 68.0]))
    assert len(trigs) == 1
    assert trigs[0]["risePts"] == 20.0
    assert trigs[0]["overMinutes"] == 5
    assert "in 5 minutes" in trigs[0]["normalityHint"]


def test_rate_change_does_NOT_fire_across_a_run_gap():
    """THE outage scenario: 88% now, 68% five and a half hours ago. Same 20-point delta, but it is
    not a 5-minute spike and must not be reported as one."""
    readings = [_r(0, 88.0), _r(330, 68.0)]          # 330 min = 5h30m
    assert _check_rate_of_change(readings) == []


def test_rate_change_tolerates_modest_jitter_but_not_a_skipped_run():
    assert _check_rate_of_change([_r(0, 88.0), _r(7, 68.0)]) != []    # 7 min: within slack
    assert _check_rate_of_change([_r(0, 88.0), _r(10, 68.0)]) == []   # 10 min: a run was skipped


def test_rate_change_silent_when_a_timestamp_is_missing_or_unparseable():
    good = _r(0, 88.0)
    assert _check_rate_of_change([good, {"peakCuPct": 68.0}]) == []
    assert _check_rate_of_change([good, dict(_r(5, 68.0), runAt="not-a-date")]) == []


def test_rate_change_states_the_REAL_elapsed_minutes():
    """Within the slack window the hint must quote the actual gap, not a hardcoded 5."""
    trigs = _check_rate_of_change([_r(0, 90.0), _r(7, 70.0)])
    assert trigs[0]["overMinutes"] == 7
    assert "in 7 minutes" in trigs[0]["normalityHint"]


# --- sustained band ---------------------------------------------------------------------

def _cfg_band(min_minutes=20.0):
    c = load_cfg()
    c["sustained_min_minutes"] = min_minutes
    return c


def test_sustained_band_fires_on_consecutive_sweeps():
    cfg = _cfg_band(20.0)                              # k = 4 readings
    trigs = _check_sustained_band(_contiguous([80.0, 78.0, 82.0, 75.0]), cfg)
    assert len(trigs) == 1 and trigs[0]["minutesInBand"] == 20


def test_sustained_band_does_NOT_fire_when_the_window_spans_a_gap():
    """Four in-band readings that happen to sit in the store, but spanning three DAYS. The
    "20+ minutes in band" claim would be false."""
    cfg = _cfg_band(20.0)
    readings = [_r(0, 80.0), _r(5, 78.0), _r(10, 82.0), _r(60 * 24 * 3, 75.0)]
    assert _check_sustained_band(readings, cfg) == []


def test_sustained_band_silent_when_timestamps_missing():
    cfg = _cfg_band(20.0)
    readings = _contiguous([80.0, 78.0, 82.0, 75.0])
    readings[2] = {"peakCuPct": 82.0}                  # no runAt
    assert _check_sustained_band(readings, cfg) == []


# --- the helper itself ------------------------------------------------------------------

def test_readings_contiguous_semantics():
    assert _readings_contiguous(_contiguous([1, 2, 3]), 3) is True
    assert _readings_contiguous(_contiguous([1, 2, 3]), 4) is False   # not enough readings
    assert _readings_contiguous([_r(0, 1), _r(330, 2)], 2) is False   # run gap
    # Out-of-order rows (older first) are rejected rather than yielding a negative gap.
    assert _readings_contiguous([_r(10, 1), _r(0, 2)], 2) is False
