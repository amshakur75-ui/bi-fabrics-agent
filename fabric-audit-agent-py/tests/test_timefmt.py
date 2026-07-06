"""Local display-time formatting for UTC telemetry timestamps (timefmt) -- offline, DST-exact."""
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


def test_display_is_eastern_daylight_in_summer():
    # 2026-07-06 is EDT (UTC-4): the 15:48 UTC throttle peak reads 11:48 AM EDT
    assert to_display("2026-07-06T15:48:00.0000000Z") == "2026-07-06 11:48 AM EDT"


def test_display_is_eastern_standard_in_winter():
    # January is EST (UTC-5) -- the conversion must be DST-aware, not a fixed offset
    assert to_display("2026-01-15T15:48:00Z") == "2026-01-15 10:48 AM EST"


def test_display_crosses_midnight_to_previous_day():
    assert to_display("2026-07-06T03:10:00Z") == "2026-07-05 11:10 PM EDT"


def test_display_noon_and_midnight_hours():
    assert to_display("2026-07-06T16:00:00Z") == "2026-07-06 12:00 PM EDT"   # noon, not 0:00
    assert to_display("2026-07-06T04:00:00Z") == "2026-07-06 12:00 AM EDT"   # midnight


def test_env_override_changes_zone(monkeypatch):
    monkeypatch.setenv("FABRIC_DISPLAY_TZ", "UTC")
    assert to_display("2026-07-06T15:48:00Z") == "2026-07-06 3:48 PM UTC"


def test_unparseable_returns_none_never_raises():
    assert to_display("t2") is None
    assert to_display(None) is None


def test_add_display_time_decorates_only_when_convertible():
    rec = {"ts": "2026-07-06T15:48:00Z"}
    add_display_time(rec, "ts", "tsLocal")
    assert rec["tsLocal"] == "2026-07-06 11:48 AM EDT"
    opaque = {"ts": "t2"}
    add_display_time(opaque, "ts", "tsLocal")
    assert "tsLocal" not in opaque             # raw value stays; display twin simply omitted


# ---- handler wiring: every timestamp the tools surface carries a display twin ----

def _no_live(monkeypatch):
    for v in ("FABRIC_CSV_PATHS", "FABRIC_CLIENT_ID", "FABRIC_KUSTO_CLUSTER",
              "FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_LA_WORKSPACE_ID"):
        monkeypatch.delenv(v, raising=False)


def test_spike_events_carry_tsLocal(monkeypatch):
    _no_live(monkeypatch)
    from fabric_audit_agent.tools import create_tool_definitions
    out = next(d for d in create_tool_definitions()
               if d["name"] == "spike_events")["handler"]({"topN": 5})
    assert out["events"], "mock fixture should yield spike events"
    for e in out["events"]:
        assert "tsLocal" in e and e["tsLocal"].endswith(("EDT", "EST"))
        assert "ts" in e   # raw UTC is preserved alongside


def test_user_spike_history_spikes_carry_tsLocal(monkeypatch):
    _no_live(monkeypatch)
    from fabric_audit_agent.tools import create_tool_definitions
    out = next(d for d in create_tool_definitions()
               if d["name"] == "user_spike_history")["handler"]({"user": "eve@co"})
    for s in out["spikes"]:
        assert "tsLocal" in s


def test_capacity_patterns_carry_windowStartLocal(monkeypatch):
    _no_live(monkeypatch)
    from fabric_audit_agent.tools import create_tool_definitions
    out = next(d for d in create_tool_definitions()
               if d["name"] == "capacity_patterns")["handler"]({})
    for p in out["patterns"]:
        assert "windowStartLocal" in p
