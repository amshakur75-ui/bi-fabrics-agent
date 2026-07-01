"""Tests for capacity_patterns() — temporal pattern engine (Task 5, Phase 3).
TDD: written before the implementation.

capacity_patterns(events, capacity_series, *, bucket_minutes=15)
  -> [{windowStart, activeUsers, cuPeakPct, drivingItem, drivingUser, kind, narrative}]

Pattern objects are emitted ONLY when an activity surge (many distinct users) coincides
with or shortly precedes a CU% spike.  Quiet windows produce no patterns.
"""
from fabric_audit_agent.investigation.patterns import capacity_patterns


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ev(ts, user, item, cu_seconds, kind="interactive"):
    """Build a minimal normalized event dict."""
    return {
        "ts": ts,
        "user": user,
        "item": item,
        "workspace": "WS1",
        "operation": "QueryEnd" if kind == "interactive" else "CommandEnd",
        "kind": kind,
        "cuSeconds": cu_seconds,
        "durationMs": int(cu_seconds * 1000),
        "throttled": False,
    }


def _cap(ts, cu_pct):
    """Build a capacity-series point."""
    return {"ts": ts, "cuPct": cu_pct}


# ---------------------------------------------------------------------------
# Core coupling scenario: surge + spike → one pattern
# ---------------------------------------------------------------------------

class TestSurgePlusSpike:
    """Burst of distinct users in one 15-min bucket + CU spike in that same bucket
    should yield exactly one pattern with high activeUsers, the correct drivingItem,
    and a narrative naming both."""

    # 8 distinct users all hitting "SalesReport" at 09:00-09:14
    SURGE_EVENTS = [
        _ev("2026-06-30T09:00:00Z", "user1@co", "SalesReport", 50.0),
        _ev("2026-06-30T09:01:00Z", "user2@co", "SalesReport", 45.0),
        _ev("2026-06-30T09:02:00Z", "user3@co", "SalesReport", 60.0),
        _ev("2026-06-30T09:03:00Z", "user4@co", "SalesReport", 55.0),
        _ev("2026-06-30T09:04:00Z", "user5@co", "SalesReport", 40.0),
        _ev("2026-06-30T09:05:00Z", "user6@co", "SalesReport", 70.0),
        _ev("2026-06-30T09:06:00Z", "user7@co", "SalesReport", 65.0),
        _ev("2026-06-30T09:07:00Z", "user8@co", "SalesReport", 80.0),
    ]

    # CU spike in the same 09:00-09:14 bucket
    SPIKE_CAPACITY = [
        _cap("2026-06-30T09:05:00Z", 92.0),   # spike in same bucket
        _cap("2026-06-30T08:45:00Z", 10.0),   # previous bucket — low
    ]

    def test_returns_exactly_one_pattern(self):
        patterns = capacity_patterns(self.SURGE_EVENTS, self.SPIKE_CAPACITY)
        assert len(patterns) == 1

    def test_active_users_equals_distinct_users(self):
        patterns = capacity_patterns(self.SURGE_EVENTS, self.SPIKE_CAPACITY)
        assert patterns[0]["activeUsers"] == 8

    def test_driving_item_is_highest_cu_item(self):
        patterns = capacity_patterns(self.SURGE_EVENTS, self.SPIKE_CAPACITY)
        assert patterns[0]["drivingItem"] == "SalesReport"

    def test_driving_user_is_present(self):
        patterns = capacity_patterns(self.SURGE_EVENTS, self.SPIKE_CAPACITY)
        # The user with the highest individual cuSeconds in the bucket
        assert patterns[0]["drivingUser"] in {f"user{i}@co" for i in range(1, 9)}

    def test_cu_peak_pct_taken_from_capacity_series(self):
        patterns = capacity_patterns(self.SURGE_EVENTS, self.SPIKE_CAPACITY)
        assert patterns[0]["cuPeakPct"] == 92.0

    def test_narrative_names_active_users(self):
        patterns = capacity_patterns(self.SURGE_EVENTS, self.SPIKE_CAPACITY)
        narrative = patterns[0]["narrative"]
        assert "8" in narrative  # 8 distinct users

    def test_narrative_names_cu_peak(self):
        patterns = capacity_patterns(self.SURGE_EVENTS, self.SPIKE_CAPACITY)
        narrative = patterns[0]["narrative"]
        # 92 or 92.0 should appear in the narrative
        assert "92" in narrative

    def test_narrative_names_driving_item(self):
        patterns = capacity_patterns(self.SURGE_EVENTS, self.SPIKE_CAPACITY)
        narrative = patterns[0]["narrative"]
        assert "SalesReport" in narrative

    def test_window_start_correct_bucket(self):
        patterns = capacity_patterns(self.SURGE_EVENTS, self.SPIKE_CAPACITY)
        # Bucket starts at 09:00 for a 15-min window
        assert "09:00" in patterns[0]["windowStart"]

    def test_kind_field_present(self):
        patterns = capacity_patterns(self.SURGE_EVENTS, self.SPIKE_CAPACITY)
        assert patterns[0]["kind"] in ("interactive", "refresh", "mixed")

    def test_required_keys_present(self):
        patterns = capacity_patterns(self.SURGE_EVENTS, self.SPIKE_CAPACITY)
        required = {"windowStart", "activeUsers", "cuPeakPct", "drivingItem", "drivingUser", "kind", "narrative"}
        assert set(patterns[0].keys()) >= required


# ---------------------------------------------------------------------------
# Surge precedes spike (lag scenario: surge at T, spike at T+1 bucket)
# ---------------------------------------------------------------------------

class TestSurgePrecedesSpike:
    """Activity surge at 09:00 bucket → CU spike in the 09:15 bucket (one bucket lag)
    should still be detected as a coupled pattern."""

    SURGE_EVENTS = [
        _ev("2026-06-30T09:00:00Z", f"user{i}@co", "InventoryDS", 30.0)
        for i in range(1, 7)   # 6 distinct users
    ]

    # CU spike in the NEXT bucket (09:15)
    CAPACITY_NEXT_BUCKET = [
        _cap("2026-06-30T09:00:00Z", 15.0),  # low at start
        _cap("2026-06-30T09:17:00Z", 88.0),  # spike in next 15-min bucket
    ]

    def test_lag_coupling_still_detected(self):
        patterns = capacity_patterns(self.SURGE_EVENTS, self.CAPACITY_NEXT_BUCKET)
        assert len(patterns) >= 1

    def test_lag_pattern_has_correct_driving_item(self):
        patterns = capacity_patterns(self.SURGE_EVENTS, self.CAPACITY_NEXT_BUCKET)
        # At least one pattern references InventoryDS
        items = [p["drivingItem"] for p in patterns]
        assert "InventoryDS" in items


# ---------------------------------------------------------------------------
# Quiet period → no patterns
# ---------------------------------------------------------------------------

class TestQuietPeriod:
    """Low user activity + low CU% → no patterns emitted."""

    QUIET_EVENTS = [
        _ev("2026-06-30T09:00:00Z", "user1@co", "SalesReport", 2.0),
        _ev("2026-06-30T09:10:00Z", "user1@co", "SalesReport", 3.0),  # same user, not a surge
    ]

    LOW_CAPACITY = [
        _cap("2026-06-30T09:05:00Z", 12.0),
    ]

    def test_quiet_period_no_patterns(self):
        patterns = capacity_patterns(self.QUIET_EVENTS, self.LOW_CAPACITY)
        assert patterns == []


# ---------------------------------------------------------------------------
# Empty inputs → empty result
# ---------------------------------------------------------------------------

class TestEdgeCasesEmpty:
    def test_empty_events_returns_empty(self):
        result = capacity_patterns([], [_cap("2026-06-30T09:00:00Z", 80.0)])
        assert result == []

    def test_empty_capacity_series_returns_empty(self):
        events = [_ev("2026-06-30T09:00:00Z", "user1@co", "Report", 10.0)]
        result = capacity_patterns(events, [])
        assert result == []

    def test_both_empty_returns_empty(self):
        assert capacity_patterns([], []) == []


# ---------------------------------------------------------------------------
# Determinism: same input → same output (stable order)
# ---------------------------------------------------------------------------

class TestDeterminism:
    EVENTS = [
        _ev("2026-06-30T09:00:00Z", "user1@co", "ReportA", 50.0),
        _ev("2026-06-30T09:02:00Z", "user2@co", "ReportA", 60.0),
        _ev("2026-06-30T09:04:00Z", "user3@co", "ReportA", 40.0),
        _ev("2026-06-30T09:06:00Z", "user4@co", "ReportA", 55.0),
        _ev("2026-06-30T09:08:00Z", "user5@co", "ReportA", 70.0),
    ]
    CAP = [_cap("2026-06-30T09:05:00Z", 85.0)]

    def test_same_input_same_output_twice(self):
        r1 = capacity_patterns(self.EVENTS, self.CAP)
        r2 = capacity_patterns(self.EVENTS, self.CAP)
        assert r1 == r2

    def test_driving_user_stable_on_tie(self):
        # When two users have equal CU, result should be deterministic (alphabetical tiebreak)
        events = [
            _ev("2026-06-30T09:00:00Z", "alpha@co", "ReportA", 50.0),
            _ev("2026-06-30T09:01:00Z", "beta@co",  "ReportA", 50.0),
            _ev("2026-06-30T09:02:00Z", "gamma@co", "ReportA", 50.0),
            _ev("2026-06-30T09:03:00Z", "delta@co", "ReportA", 50.0),
            _ev("2026-06-30T09:04:00Z", "epsilon@co","ReportA", 50.0),
        ]
        cap = [_cap("2026-06-30T09:05:00Z", 90.0)]
        r1 = capacity_patterns(events, cap)
        r2 = capacity_patterns(events, cap)
        assert r1 == r2
        if r1:
            assert r1[0]["drivingUser"] == r2[0]["drivingUser"]


# ---------------------------------------------------------------------------
# Multiple buckets: only the surge+spike bucket yields a pattern
# ---------------------------------------------------------------------------

class TestMultiBucketOnlySpikeBucketMatches:
    """Events in two buckets; only the bucket with a surge + CU spike gets a pattern."""

    # Bucket 1 (09:00): 6 users → surge
    # Bucket 2 (10:00): 1 user → quiet
    EVENTS = (
        [_ev("2026-06-30T09:00:00Z", f"u{i}@co", "BigReport", 30.0) for i in range(1, 7)] +
        [_ev("2026-06-30T10:00:00Z", "solo@co", "BigReport", 5.0)]
    )

    # CU spike only in 09:00 bucket
    CAPACITY = [
        _cap("2026-06-30T09:10:00Z", 87.0),
        _cap("2026-06-30T10:05:00Z", 8.0),
    ]

    def test_only_surge_bucket_yields_pattern(self):
        patterns = capacity_patterns(self.EVENTS, self.CAPACITY)
        # Should have exactly 1 pattern, from the 09:00 surge+spike bucket
        assert len(patterns) == 1
        assert "09:00" in patterns[0]["windowStart"]

    def test_quiet_bucket_not_emitted(self):
        patterns = capacity_patterns(self.EVENTS, self.CAPACITY)
        window_starts = [p["windowStart"] for p in patterns]
        assert not any("10:00" in ws for ws in window_starts)


# ---------------------------------------------------------------------------
# kind majority
# ---------------------------------------------------------------------------

class TestKindMajority:
    def test_interactive_majority(self):
        events = [
            _ev("2026-06-30T09:00:00Z", f"u{i}@co", "Report", 10.0, kind="interactive")
            for i in range(1, 6)
        ] + [_ev("2026-06-30T09:01:00Z", "refresh_user@co", "Report", 10.0, kind="refresh")]
        cap = [_cap("2026-06-30T09:05:00Z", 85.0)]
        patterns = capacity_patterns(events, cap)
        if patterns:
            # majority is interactive
            assert patterns[0]["kind"] in ("interactive", "mixed")

    def test_refresh_majority(self):
        events = [
            _ev("2026-06-30T09:00:00Z", f"u{i}@co", "DS", 10.0, kind="refresh")
            for i in range(1, 6)
        ] + [_ev("2026-06-30T09:01:00Z", "one_interactive@co", "DS", 10.0, kind="interactive")]
        cap = [_cap("2026-06-30T09:05:00Z", 85.0)]
        patterns = capacity_patterns(events, cap)
        if patterns:
            assert patterns[0]["kind"] in ("refresh", "mixed")
