"""N23 regression: capacity_overloads must return ONLY over-threshold windows within the
requested calendar day (UTC), even when the underlying CU-series pull uses a wide
``ago(<lookback>)`` clause because it can't express ``between(...)`` server-side.

The bug: for a single-day request N days in the past, ``_series_window(start, end)`` produced
``ago(N days)`` and ``_capacity_series_only`` returned the full over-pulled series unfiltered.
``capacity_overloads_handler`` then iterated every point through ``_overload_windows``, so the
tool returned N days of over-100% windows for a 1-day request. Two live transcripts confirmed
this at 1-day and 20-day severity.

Fix: ``_capacity_series_only`` now calls the module-level ``_clip_series_to_window`` helper
at the source when both start AND end are given. This test pins that clip behavior end-to-end
by combining the clip helper with the real ``overload_windows`` implementation -- the same
pipeline the handler runs, minus the wrapping I/O layer that would otherwise force us to stub
out a live Kusto client just to check pure post-processing."""
from datetime import datetime, timedelta, timezone

from fabric_audit_agent.tools import _clip_series_to_window
from fabric_audit_agent.investigation.overloads import overload_windows


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_bounds(dt):
    """Match _calendar_day_bounds shape: [start_day, start_day + 1 day) as ISO Z strings."""
    day = dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    return _iso(day), _iso(day + timedelta(days=1))


# ---------------------------------------------------------------------------
# The clip helper -- direct unit tests
# ---------------------------------------------------------------------------

def test_clip_keeps_only_points_inside_half_open_window():
    start_dt = datetime(2026, 7, 8, 0, 0, 0, tzinfo=timezone.utc)
    series = [
        {"ts": _iso(start_dt - timedelta(hours=1)), "cuPct": 50.0},   # BEFORE window
        {"ts": _iso(start_dt), "cuPct": 90.0},                        # ON the left edge -- INCLUDED
        {"ts": _iso(start_dt + timedelta(hours=12)), "cuPct": 137.4}, # MIDDLE
        {"ts": _iso(start_dt + timedelta(days=1) - timedelta(seconds=30)), "cuPct": 120.0},  # last 30s window
        {"ts": _iso(start_dt + timedelta(days=1)), "cuPct": 200.0},   # ON the right edge -- EXCLUDED (half-open)
        {"ts": _iso(start_dt + timedelta(days=1, hours=1)), "cuPct": 400.0},  # AFTER
    ]
    start, end = _day_bounds(start_dt)
    clipped = _clip_series_to_window(series, start, end)
    kept_ts = [pt["ts"] for pt in clipped]
    assert kept_ts == [
        _iso(start_dt),
        _iso(start_dt + timedelta(hours=12)),
        _iso(start_dt + timedelta(days=1) - timedelta(seconds=30)),
    ]


def test_clip_drops_points_with_unparseable_or_missing_ts():
    """Robustness: garbage in, no garbage out. A point with a missing/malformed ts is dropped
    rather than kept in an unknown position -- the alternative (assuming it belongs) could
    silently smuggle out-of-window rows past the clip."""
    start, end = _day_bounds(datetime(2026, 7, 8, tzinfo=timezone.utc))
    series = [
        {"ts": None, "cuPct": 50.0},
        {"cuPct": 50.0},                              # missing ts entirely
        {"ts": "not-a-date", "cuPct": 50.0},
        "not-even-a-dict",
        {"ts": "2026-07-08T06:00:00Z", "cuPct": 90.0},  # the only well-formed, in-window entry
    ]
    clipped = _clip_series_to_window(series, start, end)
    assert clipped == [{"ts": "2026-07-08T06:00:00Z", "cuPct": 90.0}]


def test_clip_returns_all_when_bounds_unparseable():
    """If start/end can't be parsed the safest thing is to hand back everything (no accidental
    silent-drop). The caller has other lines of defense (KQL bounds, meta labels)."""
    series = [{"ts": "2026-07-08T06:00:00Z", "cuPct": 90.0}]
    assert _clip_series_to_window(series, "not-a-date", "also-not-a-date") == series


def test_clip_handles_empty_and_none():
    assert _clip_series_to_window([], "2026-07-08T00:00:00Z", "2026-07-09T00:00:00Z") == []
    assert _clip_series_to_window(None, "2026-07-08T00:00:00Z", "2026-07-09T00:00:00Z") == []


# ---------------------------------------------------------------------------
# Integration: clip + overload_windows together mimics the fixed handler pipeline
# ---------------------------------------------------------------------------

def test_clip_then_overload_windows_returns_only_requested_day():
    """The scenario from Transcript 2 in GAPS-AND-ISSUES.md N23: request a date 20 days back
    with a synthetic series covering that whole 21-day lookback (one over-100% window per day
    at 12:00 UTC). Before the fix, the handler passed the 21-day series unmodified to
    overload_windows and returned 21 over-threshold entries. After the fix (clip first), it
    returns EXACTLY the ones inside the requested calendar day."""
    now = datetime.now(timezone.utc)
    requested_date = (now - timedelta(days=20)).replace(hour=0, minute=0, second=0, microsecond=0)
    # 21 candidate over-threshold windows, one per day for the full ago(21d) lookback the buggy
    # _series_window would have generated.
    synthetic_series = []
    for offset in range(-20, 1):
        w_ts = requested_date + timedelta(days=offset, hours=12)
        synthetic_series.append({"ts": _iso(w_ts), "cuPct": 137.4})

    start, end = _day_bounds(requested_date)
    clipped = _clip_series_to_window(synthetic_series, start, end)

    # After the clip, exactly one window (the 12:00 UTC one on the requested day) remains.
    assert len(clipped) == 1
    assert clipped[0]["ts"] == _iso(requested_date + timedelta(hours=12))

    # Feed through overload_windows (with parsed epochs, same shape the handler builds) and
    # confirm the tool would return exactly that one over-100% window -- not 21.
    from fabric_audit_agent.timefmt import parse_iso_utc
    series_epoch = [{"epoch": parse_iso_utc(pt["ts"]).timestamp(), "cuPct": pt["cuPct"]}
                    for pt in clipped]
    result = overload_windows(series_epoch, [], base_cu=1024, min_cu_pct=100.0)
    assert len(result) == 1
    assert result[0]["totalCuPct"] == 137.4
