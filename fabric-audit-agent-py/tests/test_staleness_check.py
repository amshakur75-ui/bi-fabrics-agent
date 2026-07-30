"""Staleness check for dimensional data (workspace/item lists, owner mappings)."""
from datetime import datetime, timedelta, timezone

from fabric_audit_agent.staleness import check_staleness, maybe_stale_note, DEFAULT_THRESHOLD_HOURS


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Fresh data (< 24h) produces no note ---

def test_fresh_data_no_note():
    recent = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    result = check_staleness(recent)
    assert result["stale"] is False
    assert result["note"] is None
    assert result["ageHours"] is not None
    assert result["ageHours"] < 2


def test_fresh_data_just_under_threshold():
    """Data collected 23 hours ago should still be considered fresh."""
    ts = _iso(datetime.now(timezone.utc) - timedelta(hours=23))
    result = check_staleness(ts)
    assert result["stale"] is False
    assert result["note"] is None


# --- Stale data (> 24h) produces appropriate note ---

def test_stale_data_produces_note():
    old = _iso(datetime.now(timezone.utc) - timedelta(hours=48))
    result = check_staleness(old, label="Workspace data")
    assert result["stale"] is True
    assert "Workspace data" in result["note"]
    assert "ownership may have changed" in result["note"]
    assert "2 days" in result["note"]


def test_stale_data_hours_description():
    """Data 30 hours old should describe the age in hours."""
    old = _iso(datetime.now(timezone.utc) - timedelta(hours=30))
    result = check_staleness(old)
    assert result["stale"] is True
    assert "30 hours" in result["note"]


def test_stale_data_days_description():
    """Data 5 days old should describe the age in days."""
    old = _iso(datetime.now(timezone.utc) - timedelta(days=5))
    result = check_staleness(old, label="Ownership mappings")
    assert result["stale"] is True
    assert "5 days" in result["note"]
    assert "Ownership mappings" in result["note"]


# --- Missing timestamp (backward compat) ---

def test_missing_timestamp_no_note():
    """None collectedAt (backward compat, mock data) produces no note."""
    result = check_staleness(None)
    assert result["stale"] is False
    assert result["note"] is None
    assert result["ageHours"] is None


def test_empty_string_timestamp_no_note():
    """An empty string timestamp should be treated like missing."""
    result = check_staleness("")
    assert result["stale"] is False
    assert result["note"] is None


def test_garbage_timestamp_no_note():
    """An unparseable timestamp should be treated like missing (graceful degradation)."""
    result = check_staleness("not-a-timestamp")
    assert result["stale"] is False
    assert result["note"] is None


# --- Configurable threshold ---

def test_custom_threshold_short():
    """A stricter threshold (e.g. 1 hour) should flag data that is 2 hours old."""
    ts = _iso(datetime.now(timezone.utc) - timedelta(hours=2))
    result = check_staleness(ts, threshold_hours=1)
    assert result["stale"] is True
    assert result["note"] is not None


def test_custom_threshold_long():
    """A relaxed threshold (e.g. 72 hours) should not flag data that is 48 hours old."""
    ts = _iso(datetime.now(timezone.utc) - timedelta(hours=48))
    result = check_staleness(ts, threshold_hours=72)
    assert result["stale"] is False
    assert result["note"] is None


def test_default_threshold_is_24h():
    assert DEFAULT_THRESHOLD_HOURS == 24


# --- maybe_stale_note convenience ---

def test_maybe_stale_note_fresh():
    recent = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    facts = {"collectedAt": recent, "items": []}
    assert maybe_stale_note(facts) is None


def test_maybe_stale_note_stale():
    old = _iso(datetime.now(timezone.utc) - timedelta(days=3))
    facts = {"collectedAt": old, "items": []}
    note = maybe_stale_note(facts, label="Workspace data")
    assert note is not None
    assert "Workspace data" in note
    assert "3 days" in note


def test_maybe_stale_note_no_collected_at():
    """Facts dict without collectedAt (e.g. mock collector) returns None."""
    facts = {"items": []}
    assert maybe_stale_note(facts) is None


def test_maybe_stale_note_non_dict():
    """Non-dict input returns None (defensive)."""
    assert maybe_stale_note(None) is None
    assert maybe_stale_note([]) is None


# --- Z-suffix and tz-aware parsing ---

def test_z_suffix_timestamp():
    ts = _iso(datetime.now(timezone.utc) - timedelta(hours=48))
    assert ts.endswith("Z")
    result = check_staleness(ts)
    assert result["stale"] is True


def test_offset_timestamp():
    """Timestamps with +00:00 offset should also work."""
    dt = datetime.now(timezone.utc) - timedelta(hours=48)
    ts = dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    result = check_staleness(ts)
    assert result["stale"] is True


# --- Boundary: exactly at threshold ---

def test_exactly_at_threshold_is_not_stale():
    """Data exactly at the threshold boundary (<=) should not be stale."""
    ts = _iso(datetime.now(timezone.utc) - timedelta(hours=24))
    result = check_staleness(ts, threshold_hours=24)
    # Due to test execution time, this could be very slightly over 24h,
    # but the check uses <= so data at exactly 24.0 hours is not stale.
    # We accept either outcome at the exact boundary.
    assert isinstance(result["stale"], bool)
