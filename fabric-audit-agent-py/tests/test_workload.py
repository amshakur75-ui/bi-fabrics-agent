"""Tests for workload.py — interactive-vs-refresh split + refresh-collision detection.
TDD: written before the implementation."""

import pytest
from fabric_audit_agent.investigation.workload import split_workload, refresh_collisions


def _ev(ts, kind, cu, item="DS1"):
    """Build a minimal normalized event dict."""
    return {
        "ts": ts,
        "user": "user@co",
        "item": item,
        "workspace": "WS1",
        "operation": "QueryEnd" if kind == "interactive" else "CommandEnd",
        "kind": kind,
        "cuSeconds": cu,
        "durationMs": int(cu * 1000),
        "throttled": False,
    }


# ---------------------------------------------------------------------------
# split_workload tests
# ---------------------------------------------------------------------------

class TestSplitWorkload:
    def test_returns_required_keys(self):
        result = split_workload([])
        for key in ("interactiveCuSeconds", "refreshCuSeconds", "interactivePct"):
            assert key in result, f"Missing key: {key}"

    def test_mixed_events_split_correctly(self):
        events = [
            _ev("2026-06-30T08:00:00Z", "interactive", 10.0),
            _ev("2026-06-30T09:00:00Z", "interactive", 20.0),
            _ev("2026-06-30T10:00:00Z", "refresh",     30.0),
            _ev("2026-06-30T11:00:00Z", "refresh",     40.0),
        ]
        result = split_workload(events)
        assert abs(result["interactiveCuSeconds"] - 30.0) < 1e-6
        assert abs(result["refreshCuSeconds"] - 70.0) < 1e-6

    def test_interactive_pct_correct(self):
        events = [
            _ev("2026-06-30T08:00:00Z", "interactive", 25.0),
            _ev("2026-06-30T09:00:00Z", "refresh",     75.0),
        ]
        result = split_workload(events)
        # 25 / (25+75) = 25%
        assert abs(result["interactivePct"] - 25.0) < 1e-6

    def test_all_interactive(self):
        events = [
            _ev("2026-06-30T08:00:00Z", "interactive", 50.0),
            _ev("2026-06-30T09:00:00Z", "interactive", 50.0),
        ]
        result = split_workload(events)
        assert abs(result["interactiveCuSeconds"] - 100.0) < 1e-6
        assert result["refreshCuSeconds"] == 0.0
        assert result["interactivePct"] == 100.0

    def test_all_refresh(self):
        events = [
            _ev("2026-06-30T08:00:00Z", "refresh", 80.0),
        ]
        result = split_workload(events)
        assert result["interactiveCuSeconds"] == 0.0
        assert abs(result["refreshCuSeconds"] - 80.0) < 1e-6
        assert result["interactivePct"] == 0.0

    def test_empty_events_zero_values(self):
        result = split_workload([])
        assert result["interactiveCuSeconds"] == 0.0
        assert result["refreshCuSeconds"] == 0.0
        assert result["interactivePct"] == 0.0

    def test_zero_total_cu_no_division_error(self):
        # Events present but all zero CU — should not raise ZeroDivisionError
        events = [
            _ev("2026-06-30T08:00:00Z", "interactive", 0.0),
            _ev("2026-06-30T09:00:00Z", "refresh", 0.0),
        ]
        result = split_workload(events)
        assert result["interactivePct"] == 0.0


# ---------------------------------------------------------------------------
# refresh_collisions tests
# ---------------------------------------------------------------------------

class TestRefreshCollisions:
    def test_refresh_inside_window_is_surfaced(self):
        events = [
            _ev("2026-06-30T19:00:00Z", "refresh",     50.0, item="SalesDS"),
            _ev("2026-06-30T19:30:00Z", "interactive",  5.0, item="SalesDS"),
        ]
        result = refresh_collisions(
            events,
            peak_start="2026-06-30T18:00:00Z",
            peak_end="2026-06-30T20:00:00Z",
        )
        assert len(result) == 1
        assert result[0]["item"] == "SalesDS"
        assert result[0]["ts"] == "2026-06-30T19:00:00Z"
        assert abs(result[0]["cuSeconds"] - 50.0) < 1e-6

    def test_refresh_outside_window_excluded(self):
        events = [
            _ev("2026-06-30T21:00:00Z", "refresh", 50.0, item="SalesDS"),  # after peak_end
            _ev("2026-06-30T17:00:00Z", "refresh", 30.0, item="InvDS"),    # before peak_start
        ]
        result = refresh_collisions(
            events,
            peak_start="2026-06-30T18:00:00Z",
            peak_end="2026-06-30T20:00:00Z",
        )
        assert result == []

    def test_interactive_events_in_window_excluded(self):
        events = [
            _ev("2026-06-30T19:00:00Z", "interactive", 100.0, item="SalesDS"),
        ]
        result = refresh_collisions(
            events,
            peak_start="2026-06-30T18:00:00Z",
            peak_end="2026-06-30T20:00:00Z",
        )
        assert result == []

    def test_boundary_timestamps_inclusive(self):
        # Exactly at peak_start and peak_end should be included
        events = [
            _ev("2026-06-30T18:00:00Z", "refresh", 10.0, item="A"),  # exactly peak_start
            _ev("2026-06-30T20:00:00Z", "refresh", 20.0, item="B"),  # exactly peak_end
        ]
        result = refresh_collisions(
            events,
            peak_start="2026-06-30T18:00:00Z",
            peak_end="2026-06-30T20:00:00Z",
        )
        assert len(result) == 2

    def test_result_shape_has_required_fields(self):
        events = [_ev("2026-06-30T19:00:00Z", "refresh", 50.0, item="DS")]
        result = refresh_collisions(
            events,
            peak_start="2026-06-30T18:00:00Z",
            peak_end="2026-06-30T20:00:00Z",
        )
        assert len(result) == 1
        entry = result[0]
        for field in ("item", "ts", "cuSeconds"):
            assert field in entry, f"Missing field: {field}"

    def test_mixed_inside_and_outside(self):
        events = [
            _ev("2026-06-30T17:59:00Z", "refresh", 10.0, item="Early"),   # before window
            _ev("2026-06-30T19:00:00Z", "refresh", 50.0, item="InWindow"), # inside
            _ev("2026-06-30T20:01:00Z", "refresh", 30.0, item="Late"),    # after window
        ]
        result = refresh_collisions(
            events,
            peak_start="2026-06-30T18:00:00Z",
            peak_end="2026-06-30T20:00:00Z",
        )
        assert len(result) == 1
        assert result[0]["item"] == "InWindow"

    def test_empty_events(self):
        result = refresh_collisions(
            [],
            peak_start="2026-06-30T18:00:00Z",
            peak_end="2026-06-30T20:00:00Z",
        )
        assert result == []

    def test_multiple_refreshes_in_window_all_returned(self):
        events = [
            _ev("2026-06-30T18:30:00Z", "refresh", 40.0, item="DS1"),
            _ev("2026-06-30T19:00:00Z", "refresh", 60.0, item="DS2"),
            _ev("2026-06-30T19:45:00Z", "refresh", 20.0, item="DS3"),
        ]
        result = refresh_collisions(
            events,
            peak_start="2026-06-30T18:00:00Z",
            peak_end="2026-06-30T20:00:00Z",
        )
        assert len(result) == 3
