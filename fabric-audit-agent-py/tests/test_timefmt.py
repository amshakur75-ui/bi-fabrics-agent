"""Canonical display-time formatting for UTC telemetry timestamps (timefmt) -- offline, DST-exact.

One format everywhere: UTC first (24h), local wall-clock in parentheses --
"2026-07-06 15:48 UTC (11:48 AM EDT)".
"""
import pytest

from fabric_audit_agent.timefmt import add_display_time, parse_iso_utc, to_display

pytest.importorskip("zoneinfo")


def test_parse_variants():
    assert parse_iso_utc("2026-07-06T15:48:00Z").hour == 15
    # Log Analytics emits 7 fractional digits -- must not choke fromisoformat
    assert parse_iso_utc("2026-07-06T16:25:38.3079171Z").second == 38
    assert parse_iso_utc("2026-07-06T15:48:00+00:00").minute == 48
    naive = parse_iso_utc("2026-07-06T15:48:00")
    assert naive.tzinfo is not None            # naive input assumed UTC
    assert parse_iso_utc("") is None
    assert parse_iso_utc(None) is None
    assert parse_iso_utc("t2") is None         # capacity-events fixture-style opaque label
    assert parse_iso_utc("not a time") is None


def test_display_utc_first_with_eastern_daylight_parenthetical():
    # 2026-07-06 is EDT (UTC-4): 15:48 UTC == 11:48 AM EDT
    assert to_display("2026-07-06T15:48:00.0000000Z") == "2026-07-06 15:48 UTC (11:48 AM EDT)"


def test_display_eastern_standard_in_winter():
    # January is EST (UTC-5) -- the conversion must be DST-aware, not a fixed offset
    assert to_display("2026-01-15T15:48:00Z") == "2026-01-15 15:48 UTC (10:48 AM EST)"


def test_display_local_date_can_differ_from_utc_date():
    assert to_display("2026-07-06T03:10:00Z") == "2026-07-06 03:10 UTC (11:10 PM EDT)"


def test_display_noon_and_midnight_hours():
    assert to_display("2026-07-06T16:00:00Z") == "2026-07-06 16:00 UTC (12:00 PM EDT)"   # noon
    assert to_display("2026-07-06T04:00:00Z") == "2026-07-06 04:00 UTC (12:00 AM EDT)"   # midnight


def test_env_override_changes_parenthetical_zone(monkeypatch):
    monkeypatch.setenv("FABRIC_DISPLAY_TZ", "UTC")
    assert to_display("2026-07-06T15:48:00Z") == "2026-07-06 15:48 UTC (3:48 PM UTC)"


def test_offset_input_normalizes_to_utc_first():
    # A non-UTC offset input still renders the UTC half correctly
    assert to_display("2026-07-06T11:48:00-04:00") == "2026-07-06 15:48 UTC (11:48 AM EDT)"


def test_unparseable_returns_none_never_raises():
    assert to_display("t2") is None
    assert to_display(None) is None


def test_add_display_time_decorates_only_when_convertible():
    rec = {"ts": "2026-07-06T15:48:00Z"}
    add_display_time(rec, "ts", "tsDisplay")
    assert rec["tsDisplay"] == "2026-07-06 15:48 UTC (11:48 AM EDT)"
    opaque = {"ts": "t2"}
    add_display_time(opaque, "ts", "tsDisplay")
    assert "tsDisplay" not in opaque           # raw value stays; display twin simply omitted


# ---- handler wiring: every timestamp the tools surface carries a display twin ----

def _no_live(monkeypatch):
    for v in ("FABRIC_CSV_PATHS", "FABRIC_CLIENT_ID", "FABRIC_KUSTO_CLUSTER",
              "FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_LA_WORKSPACE_ID"):
        monkeypatch.delenv(v, raising=False)


def test_spike_events_carry_tsDisplay(monkeypatch):
    _no_live(monkeypatch)
    from fabric_audit_agent.tools import create_tool_definitions
    out = next(d for d in create_tool_definitions()
               if d["name"] == "spike_events")["handler"]({"topN": 5})
    assert out["events"], "mock fixture should yield spike events"
    for e in out["events"]:
        assert " UTC (" in e["tsDisplay"]      # canonical: UTC first, local parenthetical
        assert "ts" in e                        # raw UTC is preserved alongside


def test_user_spike_history_spikes_carry_tsDisplay(monkeypatch):
    _no_live(monkeypatch)
    from fabric_audit_agent.tools import create_tool_definitions
    out = next(d for d in create_tool_definitions()
               if d["name"] == "user_spike_history")["handler"]({"user": "eve@co"})
    for s in out["spikes"]:
        assert " UTC (" in s["tsDisplay"]


def test_capacity_patterns_carry_windowStartDisplay(monkeypatch):
    _no_live(monkeypatch)
    from fabric_audit_agent.tools import create_tool_definitions
    out = next(d for d in create_tool_definitions()
               if d["name"] == "capacity_patterns")["handler"]({})
    for p in out["patterns"]:
        assert " UTC (" in p["windowStartDisplay"]


def test_system_prompt_mandates_verbatim_display_fields():
    from fabric_audit_agent.agent.system_prompt import build_system_prompt
    p = build_system_prompt()
    assert "whenDisplay" in p and "tsDisplay" in p
    assert "VERBATIM" in p
    assert "NEVER convert timezones" in p
