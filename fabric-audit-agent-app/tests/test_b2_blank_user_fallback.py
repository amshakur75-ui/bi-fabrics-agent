"""B2 blank-user fallback — Task 4.1.

Tests for the two-tier blank-user resolution in ``rollup_attribution``:
  - Direct user (already present) gets ``attributionSource: "direct"``
  - Tier 1 resolves from Activity Events cross-reference (item + timestamp match)
  - Tier 2 resolves from item owner when Activity Events has nothing
  - Unresolved stays blank with ``attributionSource: "unresolved"``
  - Never fabricates an identity — only real data from real sources
"""
from fabric_audit_agent.adapters.attribution_rollup import (
    rollup_attribution, resolve_blank_user, _parse_ts, _ts_delta_seconds, _stronger_source,
)


# ── Helper unit tests ────────────────────────────────────────────


class TestStrongerSource:
    def test_direct_beats_all(self):
        assert _stronger_source("direct", "activity-crossref") == "direct"
        assert _stronger_source("direct", "item-owner") == "direct"
        assert _stronger_source("direct", "unresolved") == "direct"

    def test_activity_crossref_beats_owner_and_unresolved(self):
        assert _stronger_source("activity-crossref", "item-owner") == "activity-crossref"
        assert _stronger_source("activity-crossref", "unresolved") == "activity-crossref"

    def test_item_owner_beats_unresolved(self):
        assert _stronger_source("item-owner", "unresolved") == "item-owner"

    def test_order_independent(self):
        assert _stronger_source("unresolved", "direct") == "direct"
        assert _stronger_source("item-owner", "activity-crossref") == "activity-crossref"


class TestParseTs:
    def test_iso_z(self):
        dt = _parse_ts("2026-07-01T06:00:00Z")
        assert dt is not None and dt.hour == 6

    def test_iso_offset(self):
        dt = _parse_ts("2026-07-01T06:00:00+00:00")
        assert dt is not None

    def test_none_and_empty(self):
        assert _parse_ts(None) is None
        assert _parse_ts("") is None

    def test_bad_string(self):
        assert _parse_ts("not-a-date") is None


class TestTsDeltaSeconds:
    def test_same_moment(self):
        a = _parse_ts("2026-07-01T06:00:00Z")
        assert _ts_delta_seconds(a, a) == 0

    def test_30s_apart(self):
        a = _parse_ts("2026-07-01T06:00:00Z")
        b = _parse_ts("2026-07-01T06:00:30Z")
        assert _ts_delta_seconds(a, b) == 30

    def test_mixed_tz_awareness(self):
        a = _parse_ts("2026-07-01T06:00:00Z")       # aware
        b = _parse_ts("2026-07-01T06:00:10")         # naive
        assert _ts_delta_seconds(a, b) == 10

    def test_none_input(self):
        assert _ts_delta_seconds(None, _parse_ts("2026-07-01T06:00:00Z")) is None


# ── resolve_blank_user unit tests ────────────────────────────────


class TestResolveBlankUser:
    def test_tier1_crossref_match(self):
        """A matching Activity Events entry within the time window resolves the user."""
        activity = [
            {"item": "Sales", "workspace": "Finance", "user": "alice@co.com",
             "time": "2026-07-01T06:00:10Z"},
        ]
        user, source = resolve_blank_user(
            "Sales", "Finance", "2026-07-01T06:00:00Z", activity, None,
        )
        assert user == "alice@co.com"
        assert source == "activity-crossref"

    def test_tier1_picks_closest_match(self):
        """When multiple Activity Events match, the closest by timestamp wins."""
        activity = [
            {"item": "Sales", "workspace": "Finance", "user": "far@co.com",
             "time": "2026-07-01T06:00:50Z"},
            {"item": "Sales", "workspace": "Finance", "user": "close@co.com",
             "time": "2026-07-01T06:00:05Z"},
        ]
        user, _ = resolve_blank_user(
            "Sales", "Finance", "2026-07-01T06:00:00Z", activity, None,
        )
        assert user == "close@co.com"

    def test_tier1_rejects_outside_window(self):
        """Events outside the ±60 s window are not matched."""
        activity = [
            {"item": "Sales", "workspace": "Finance", "user": "alice@co.com",
             "time": "2026-07-01T06:05:00Z"},  # 300 s away
        ]
        user, source = resolve_blank_user(
            "Sales", "Finance", "2026-07-01T06:00:00Z", activity, None,
        )
        assert user is None
        assert source == "unresolved"

    def test_tier1_workspace_mismatch_excluded(self):
        """Activity Events for a different workspace are not matched even if item matches."""
        activity = [
            {"item": "Sales", "workspace": "HR", "user": "alice@co.com",
             "time": "2026-07-01T06:00:05Z"},
        ]
        user, source = resolve_blank_user(
            "Sales", "Finance", "2026-07-01T06:00:00Z", activity, None,
        )
        assert user is None
        assert source == "unresolved"

    def test_tier1_skips_blank_activity_user(self):
        """Activity Events entries with blank users are skipped (never fabricate)."""
        activity = [
            {"item": "Sales", "workspace": "Finance", "user": "",
             "time": "2026-07-01T06:00:05Z"},
        ]
        user, source = resolve_blank_user(
            "Sales", "Finance", "2026-07-01T06:00:00Z", activity, None,
        )
        assert user is None
        assert source == "unresolved"

    def test_tier2_owner_fallback(self):
        """When no Activity Events match, item owner is used."""
        items = [{"name": "Sales", "workspace": "Finance", "configuredBy": "owner@co.com"}]
        user, source = resolve_blank_user("Sales", "Finance", None, None, items)
        assert user == "owner@co.com"
        assert source == "item-owner"

    def test_tier2_owner_field(self):
        """Also works when the field is called ``owner`` (not ``configuredBy``)."""
        items = [{"name": "Sales", "workspace": "Finance", "owner": "boss@co.com"}]
        user, source = resolve_blank_user("Sales", "Finance", None, None, items)
        assert user == "boss@co.com"
        assert source == "item-owner"

    def test_tier2_workspace_mismatch_excluded(self):
        items = [{"name": "Sales", "workspace": "HR", "owner": "boss@co.com"}]
        user, source = resolve_blank_user("Sales", "Finance", None, None, items)
        assert user is None
        assert source == "unresolved"

    def test_unresolved_when_no_fallback_data(self):
        user, source = resolve_blank_user("Sales", "Finance", None, None, None)
        assert user is None
        assert source == "unresolved"

    def test_tier1_beats_tier2(self):
        """When both tiers could match, tier 1 (Activity Events) wins."""
        activity = [
            {"item": "Sales", "workspace": "Finance", "user": "actual@co.com",
             "time": "2026-07-01T06:00:05Z"},
        ]
        items = [{"name": "Sales", "workspace": "Finance", "owner": "owner@co.com"}]
        user, source = resolve_blank_user(
            "Sales", "Finance", "2026-07-01T06:00:00Z", activity, items,
        )
        assert user == "actual@co.com"
        assert source == "activity-crossref"


# ── rollup_attribution integration tests ─────────────────────────


class TestRollupDirectUser:
    """Events with an existing user carry ``attributionSource: "direct"``."""

    def test_direct_user_tagged_on_item_top_users(self):
        rows = [{"ItemName": "M", "ExecutingUser": "a@x.com", "DurationMs": 300}]
        out = rollup_attribution(rows)
        top = out["items"][0]["topUsers"][0]
        assert top["user"] == "a@x.com"
        assert top["attributionSource"] == "direct"

    def test_direct_user_tagged_on_users_list(self):
        rows = [{"ItemName": "M", "ExecutingUser": "a@x.com", "DurationMs": 300}]
        out = rollup_attribution(rows)
        assert out["users"][0]["attributionSource"] == "direct"

    def test_multiple_direct_users(self):
        rows = [
            {"ItemName": "M", "ExecutingUser": "a@x.com", "DurationMs": 300},
            {"ItemName": "M", "ExecutingUser": "b@x.com", "DurationMs": 100},
        ]
        out = rollup_attribution(rows)
        sources = {u["user"]: u["attributionSource"] for u in out["items"][0]["topUsers"]}
        assert sources == {"a@x.com": "direct", "b@x.com": "direct"}


class TestRollupTier1Crossref:
    """Blank-user rows resolved via Activity Events cross-reference."""

    def test_crossref_resolves_and_tags(self):
        rows = [
            {"ItemName": "Sales", "WorkspaceName": "Finance",
             "DurationMs": 500, "TimeGenerated": "2026-07-01T06:00:00Z"},
        ]
        activity = [
            {"item": "Sales", "workspace": "Finance", "user": "alice@co.com",
             "time": "2026-07-01T06:00:10Z"},
        ]
        out = rollup_attribution(rows, activity_events=activity)
        top = out["items"][0]["topUsers"][0]
        assert top["user"] == "alice@co.com"
        assert top["attributionSource"] == "activity-crossref"
        assert out["users"][0]["attributionSource"] == "activity-crossref"

    def test_crossref_cu_attributed_correctly(self):
        """The resolved user gets the CU from the blank-user row."""
        rows = [
            {"ItemName": "Sales", "WorkspaceName": "Finance",
             "DurationMs": 2000, "TimeGenerated": "2026-07-01T06:00:00Z"},
        ]
        activity = [
            {"item": "Sales", "workspace": "Finance", "user": "alice@co.com",
             "time": "2026-07-01T06:00:05Z"},
        ]
        out = rollup_attribution(rows, activity_events=activity)
        assert out["users"][0]["cuSeconds"] == 2.0   # 2000ms -> 2.0 CU-seconds

    def test_mixed_direct_and_crossref(self):
        """Same user appears via direct and crossref: strongest source wins (direct)."""
        rows = [
            {"ItemName": "Sales", "WorkspaceName": "Finance",
             "ExecutingUser": "alice@co.com", "DurationMs": 300},
            {"ItemName": "Sales", "WorkspaceName": "Finance",
             "DurationMs": 200, "TimeGenerated": "2026-07-01T06:00:00Z"},
        ]
        activity = [
            {"item": "Sales", "workspace": "Finance", "user": "alice@co.com",
             "time": "2026-07-01T06:00:05Z"},
        ]
        out = rollup_attribution(rows, activity_events=activity)
        # Same user; strongest source is "direct".
        assert out["items"][0]["topUsers"][0]["attributionSource"] == "direct"
        assert out["users"][0]["attributionSource"] == "direct"
        assert out["users"][0]["cuSeconds"] == 0.5   # 300 + 200 ms -> 0.5s


class TestRollupTier2Owner:
    """Blank-user rows resolved via item owner when Activity Events has nothing."""

    def test_owner_fallback_resolves(self):
        rows = [
            {"ItemName": "Sales", "WorkspaceName": "Finance", "DurationMs": 1000},
        ]
        items_meta = [{"name": "Sales", "workspace": "Finance", "configuredBy": "owner@co.com"}]
        out = rollup_attribution(rows, items_metadata=items_meta)
        top = out["items"][0]["topUsers"][0]
        assert top["user"] == "owner@co.com"
        assert top["attributionSource"] == "item-owner"
        assert out["users"][0]["attributionSource"] == "item-owner"

    def test_owner_field_variant(self):
        """Works with ``owner`` field name (not just ``configuredBy``)."""
        rows = [{"ItemName": "Sales", "WorkspaceName": "Finance", "DurationMs": 1000}]
        items_meta = [{"name": "Sales", "workspace": "Finance", "owner": "boss@co.com"}]
        out = rollup_attribution(rows, items_metadata=items_meta)
        assert out["items"][0]["topUsers"][0]["user"] == "boss@co.com"
        assert out["items"][0]["topUsers"][0]["attributionSource"] == "item-owner"


class TestRollupUnresolved:
    """Blank-user rows that neither tier resolves."""

    def test_unresolved_no_user_in_topusers(self):
        """Without fallback data, blank users produce no user entries (same as before)."""
        rows = [
            {"ItemName": "Sales", "WorkspaceName": "Finance", "DurationMs": 1000},
        ]
        out = rollup_attribution(rows)
        assert out["items"][0]["topUsers"] == []
        assert out["items"][0]["userCount"] == 0
        assert out["users"] == []

    def test_unresolved_item_still_has_cu(self):
        """The item's CU total still includes the anonymous row."""
        rows = [
            {"ItemName": "Sales", "WorkspaceName": "Finance", "DurationMs": 1000},
        ]
        out = rollup_attribution(rows)
        assert out["items"][0]["cuSeconds"] == 1.0

    def test_unresolved_with_empty_activity_and_items(self):
        """Explicitly passing empty fallback data still yields unresolved."""
        rows = [{"ItemName": "Sales", "WorkspaceName": "Finance", "DurationMs": 500}]
        out = rollup_attribution(rows, activity_events=[], items_metadata=[])
        assert out["items"][0]["topUsers"] == []
        assert out["users"] == []


class TestNoFabricatedIdentity:
    """Guard: the fallback never invents a user that doesn't exist in a real source."""

    def test_blank_activity_user_not_adopted(self):
        """An Activity Events entry with a blank user is never used as a resolved identity."""
        rows = [
            {"ItemName": "Sales", "WorkspaceName": "Finance",
             "DurationMs": 500, "TimeGenerated": "2026-07-01T06:00:00Z"},
        ]
        activity = [
            {"item": "Sales", "workspace": "Finance", "user": "",
             "time": "2026-07-01T06:00:05Z"},
        ]
        out = rollup_attribution(rows, activity_events=activity)
        assert out["items"][0]["topUsers"] == []

    def test_blank_owner_not_adopted(self):
        """An item with a blank owner is not used as a resolved identity."""
        rows = [{"ItemName": "Sales", "WorkspaceName": "Finance", "DurationMs": 500}]
        items_meta = [{"name": "Sales", "workspace": "Finance", "owner": ""}]
        out = rollup_attribution(rows, items_metadata=items_meta)
        assert out["items"][0]["topUsers"] == []


class TestBackwardCompatibility:
    """Existing callers (no fallback data) must produce identical output structure."""

    def test_existing_rollup_shape_unchanged(self):
        rows = [
            {"ItemName": "M", "WorkspaceName": "WS", "Identity": {"Email": "a@x.com"},
             "DurationMs": 300},
            {"ItemName": "M", "WorkspaceName": "WS", "Identity": {"Email": "b@x.com"},
             "DurationMs": 100},
        ]
        out = rollup_attribution(rows)
        item = out["items"][0]
        assert item["name"] == "M" and item["cuSeconds"] == 0.4
        assert item["topUsers"][0]["user"] == "a@x.com"
        # New field is present and set to "direct".
        assert item["topUsers"][0]["attributionSource"] == "direct"
        assert out["users"][0]["attributionSource"] == "direct"

    def test_empty_input(self):
        assert rollup_attribution([]) == {"items": [], "users": []}
        assert rollup_attribution(None) == {"items": [], "users": []}


def test_a_real_log_analytics_timestamp_with_seven_fractional_digits_parses():
    """FIXTURE REALISM, the recurring hazard here. Real LA ``TimeGenerated`` values carry SEVEN
    fractional digits. Python 3.10's ``fromisoformat`` -- and the serverless JOB COMPUTE runs 3.10,
    which is where LA rows are collected -- accepts only 3 or 6, so the hand-rolled parser raised on
    every real row, returned None, and silently skipped the activity cross-reference. It worked
    locally on 3.12 and in the App on 3.11, so nothing surfaced it. Every fixture in this file used
    a clean 0- or 3-digit form.
    """
    from fabric_audit_agent.adapters.attribution_rollup import _parse_ts

    for raw in ("2026-08-05T13:52:07.3079171Z",      # the real shape, 7 digits
                "2026-08-05T13:52:07.307Z",          # 3
                "2026-08-05T13:52:07Z",              # none
                "2026-08-05T13:52:07.307917Z"):      # 6
        got = _parse_ts(raw)
        assert got is not None, f"{raw!r} failed to parse"
        assert got.year == 2026 and got.hour == 13 and got.minute == 52
    assert _parse_ts("not a timestamp") is None
    assert _parse_ts(None) is None
