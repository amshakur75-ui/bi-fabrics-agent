"""Tests for query.windows.resolve_window — real sub-day / absolute time windows.

Precedence: start+end > hours > days > default 30d. Python 3.10's datetime.fromisoformat
is NOT "Z"-tolerant (that landed in 3.11), so resolve_window must normalize "Z" -> "+00:00"
BEFORE parsing -- these tests must pass on 3.10 even though local dev may run newer.
"""
import pytest

from fabric_audit_agent.query.windows import resolve_window


# ---------------------------------------------------------------------------
# Precedence tier: default (all None)
# ---------------------------------------------------------------------------

def test_default_is_30d():
    out = resolve_window()
    assert out["clause"] == "| where TimeGenerated > ago(30d)"
    assert out["label"] == "last 30d"


# ---------------------------------------------------------------------------
# Precedence tier: days
# ---------------------------------------------------------------------------

def test_days_emits_ago_d_clause():
    out = resolve_window(days=7)
    assert out["clause"] == "| where TimeGenerated > ago(7d)"
    assert out["label"] == "last 7d"


def test_days_zero_is_not_treated_as_unset():
    # 0 is a valid, meaningful lookback (not "unset") -- must not fall through to default 30d.
    out = resolve_window(days=0)
    assert out["clause"] == "| where TimeGenerated > ago(0d)"
    assert out["label"] == "last 0d"


# ---------------------------------------------------------------------------
# Precedence tier: hours (overrides days)
# ---------------------------------------------------------------------------

def test_hours_emits_ago_h_clause():
    out = resolve_window(hours=6)
    assert out["clause"] == "| where TimeGenerated > ago(6h)"
    assert out["label"] == "last 6h"


def test_hours_fractional_supports_last_n_minutes():
    # 0.25h = 15 min -- "right now" style queries.
    out = resolve_window(hours=0.25)
    assert out["clause"] == "| where TimeGenerated > ago(0.25h)"
    assert "15" in out["label"] or "0.25h" in out["label"]


def test_hours_overrides_days_when_both_given():
    out = resolve_window(days=30, hours=2)
    assert out["clause"] == "| where TimeGenerated > ago(2h)"
    assert out["label"] == "last 2h"


def test_hours_zero_is_not_treated_as_unset():
    out = resolve_window(hours=0)
    assert out["clause"] == "| where TimeGenerated > ago(0h)"


# ---------------------------------------------------------------------------
# Precedence tier: start+end (highest priority; overrides hours/days)
# ---------------------------------------------------------------------------

def test_start_and_end_emit_between_clause_with_z_suffix():
    out = resolve_window(start="2026-07-05T12:45:00Z", end="2026-07-05T13:00:00Z")
    assert (
        "between (datetime(2026-07-05T12:45:00Z) .. datetime(2026-07-05T13:00:00Z))"
        in out["clause"]
    )
    # Must NOT emit +00:00 -- the brief requires a literal Z suffix.
    assert "+00:00" not in out["clause"]


def test_start_end_label_is_human_readable():
    out = resolve_window(start="2026-07-05T12:45:00Z", end="2026-07-05T13:00:00Z")
    assert out["label"] == "2026-07-05T12:45:00Z..13:00:00Z"


def test_start_end_overrides_hours_and_days():
    out = resolve_window(days=30, hours=2, start="2026-07-05T12:45:00Z", end="2026-07-05T13:00:00Z")
    assert "between (" in out["clause"]


def test_start_end_parses_without_z_suffix_input_too():
    # An explicit UTC offset input must also work (not just literal "Z").
    out = resolve_window(start="2026-07-05T12:45:00+00:00", end="2026-07-05T13:00:00+00:00")
    assert (
        "between (datetime(2026-07-05T12:45:00Z) .. datetime(2026-07-05T13:00:00Z))"
        in out["clause"]
    )


def test_start_end_converts_non_utc_offset_to_utc():
    # +02:00 -> should convert to UTC (10:45 local -2h = 08:45Z).
    out = resolve_window(start="2026-07-05T12:45:00+02:00", end="2026-07-05T13:00:00+02:00")
    assert "datetime(2026-07-05T10:45:00Z)" in out["clause"]
    assert "datetime(2026-07-05T11:00:00Z)" in out["clause"]


def test_start_without_end_does_not_use_between_clause():
    # Only BOTH start and end together trigger the between-clause tier; a lone start
    # doesn't have a complete window, so precedence falls through to hours/days/default.
    out = resolve_window(start="2026-07-05T12:45:00Z")
    assert "between (" not in out["clause"]
    assert out["clause"] == "| where TimeGenerated > ago(30d)"


def test_end_without_start_does_not_use_between_clause():
    out = resolve_window(end="2026-07-05T13:00:00Z")
    assert "between (" not in out["clause"]
    assert out["clause"] == "| where TimeGenerated > ago(30d)"


# ---------------------------------------------------------------------------
# Malformed ISO -> ValueError (not a crash further downstream)
# ---------------------------------------------------------------------------

def test_malformed_start_raises_value_error():
    with pytest.raises(ValueError):
        resolve_window(start="not-a-date", end="2026-07-05T13:00:00Z")


def test_malformed_end_raises_value_error():
    with pytest.raises(ValueError):
        resolve_window(start="2026-07-05T12:45:00Z", end="also-not-a-date")


def test_malformed_start_error_message_is_clear():
    with pytest.raises(ValueError, match="start"):
        resolve_window(start="garbage", end="2026-07-05T13:00:00Z")
