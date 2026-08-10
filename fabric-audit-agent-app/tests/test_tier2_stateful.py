"""Step 2 stateful gates + the tier2_readings rolling store."""
from datetime import datetime, timedelta, timezone

from fabric_audit_agent.context_readings import create_readings_store_memory
from fabric_audit_agent.automation.tier2_check import (
    _check_sustained_band, _check_rate_of_change, _check_silent_failure, run_tier2_check)

_BASE = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _r(run_at, peak, ok=True):
    """One readings row. ``run_at`` may be an ISO string OR a bare index/label.

    FIXTURE REALISM: these fixtures used to pass placeholder labels ("t0", "t1") for runAt, but
    production ALWAYS writes ISO-8601 (`_record_reading` stamps `_now_iso()`). The stateful gates
    now require contiguous, parseable timestamps to avoid asserting durations that never happened
    (see tests/test_stateful_gate_continuity.py), so a label-shaped fixture is no longer a valid
    stand-in for a real reading. A bare "t<N>" label is therefore translated into a realistic
    timestamp N sweeps back from a fixed base — preserving each test's intent (index 0 = newest)
    while matching the shape production emits.
    """
    # Label convention, uniform across this file: "t<N>" is N sweeps OLD, so t0 is the newest
    # row and a newest-first list reads t0, t1, t2 ... (matching what ``recent()`` returns).
    if isinstance(run_at, str) and run_at.startswith("t") and run_at[1:].isdigit():
        run_at = (_BASE - timedelta(minutes=5 * int(run_at[1:]))).isoformat().replace("+00:00", "Z")
    return {"runAt": run_at, "peakCuPct": peak, "collectorOk": ok}


# ---- store ----

def test_readings_store_recent_is_newest_first():
    s = create_readings_store_memory()
    for i in range(5):
        s["append"](_r(f"2026-08-04T10:0{i}:00Z", 50 + i))
    recent = s["recent"](3)
    assert [x["peakCuPct"] for x in recent] == [54, 53, 52]   # newest first, capped at 3


# ---- sustained band (default 70-90 for 20min = 4 windows) ----

def test_sustained_fires_when_band_held_for_four_windows():
    readings = [_r(f"t{i}", p) for i, p in enumerate([82, 80, 78, 75])]  # newest-first, all in band
    trigs = _check_sustained_band(readings)
    assert len(trigs) == 1 and trigs[0]["check"] == "sustained" and trigs[0]["minutesInBand"] == 20


def test_sustained_silent_if_one_window_out_of_band():
    readings = [_r(f"t{i}", p) for i, p in enumerate([82, 80, 65, 75])]  # 65 is below the band
    assert _check_sustained_band(readings) == []


def test_sustained_silent_without_enough_history():
    assert _check_sustained_band([_r("t0", 80), _r("t1", 80)]) == []   # only 2 < 4 windows


# ---- rate of change (default +15 pts) ----

def test_rate_of_change_fires_on_sharp_climb():
    trigs = _check_rate_of_change([_r("t0", 60), _r("t1", 40)])   # newest 60, prev 40 -> +20
    assert len(trigs) == 1 and trigs[0]["risePts"] == 20.0 and trigs[0]["check"] == "rate_change"


def test_rate_of_change_silent_on_gentle_move():
    assert _check_rate_of_change([_r("t0", 45), _r("t1", 40)]) == []   # +5 < 15


# ---- silent failure (default 3 runs) ----

def test_silent_failure_fires_after_three_blind_runs():
    readings = [_r("t0", None, ok=False), _r("t1", None, ok=False), _r("t2", None, ok=False)]
    trigs = _check_silent_failure(readings)
    assert len(trigs) == 1 and trigs[0]["check"] == "silent_failure" and trigs[0]["runs"] == 3


def test_silent_failure_silent_if_any_recent_run_ok():
    readings = [_r("t0", None, ok=False), _r("t1", 50, ok=True), _r("t2", None, ok=False)]
    assert _check_silent_failure(readings) == []


# ---- run_tier2_check integration: reading is recorded; silent-failure on collector failure ----

def test_run_records_reading_and_evaluates_stateful():
    store = create_readings_store_memory()
    facts = {"capacity": {"peakCuPct": 80.0}, "items": []}
    out = run_tier2_check({"collect": lambda: facts}, readings_store=store,
                          now_dt=None)
    assert store["_data"] and store["_data"][-1]["peakCuPct"] == 80.0
    assert store["_data"][-1]["collectorOk"] is True


def test_run_records_failed_reading_and_fires_silent_failure():
    # pre-seed two prior blind runs, then a third failing collector -> silent_failure fires
    store = create_readings_store_memory(initial=[
        {"runAt": "2026-08-04T09:50:00Z", "peakCuPct": None, "collectorOk": False},
        {"runAt": "2026-08-04T09:55:00Z", "peakCuPct": None, "collectorOk": False},
    ])

    def boom():
        raise RuntimeError("kusto down")

    out = run_tier2_check({"collect": boom}, readings_store=store)
    assert out["error"] == "collector failed"
    assert [t["check"] for t in out["triggers"]] == ["silent_failure"]
