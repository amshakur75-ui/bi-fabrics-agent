"""Human-readable display strings for raw UTC telemetry timestamps. Stdlib only.

Telemetry timestamps are ISO-8601 UTC (e.g. ``2026-07-06T15:48:00.0000000Z``). The tools keep
those raw values for machine use and attach a display twin (``tsDisplay``/``whenDisplay``/...)
built here in ONE canonical format — UTC first, local wall-clock in parentheses:

    2026-07-06 15:48 UTC (11:48 AM EDT)

so every surfaced time reads identically and the agent never does its own timezone math — LLM
DST arithmetic is exactly the kind of silent numeric error the honesty rules exist to prevent.

Local zone comes from ``FABRIC_DISPLAY_TZ`` (IANA name; default ``America/New_York``). Conversion
uses ``zoneinfo`` so EDT/EST daylight-saving transitions are correct; the label is the zone's own
abbreviation (``EDT``/``EST``). If the tz database is missing the parenthetical is omitted (the
UTC half still renders); an unparseable timestamp yields None and callers omit the field — the
raw value is always still there.
"""
import os
import re
from datetime import datetime, timezone

_DEFAULT_TZ = "America/New_York"

# fromisoformat() (3.11+) tolerates most ISO-8601, but not >6 fractional-second digits —
# Log Analytics emits 7 (e.g. ".3079171Z"). Trim to microseconds before parsing.
_FRACTION_TRIM = re.compile(r"(\.\d{6})\d+")

# Our own canonical display form, accepted back as input ("2026-07-06 15:48 UTC (11:48 AM EDT)"
# or just "2026-07-06 15:48 UTC") — the agent naturally echoes it into `when` arguments.
_DISPLAY_FORM = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?\s*UTC\b")


def parse_iso_utc(ts):
    """Parse an ISO-8601 string — or our canonical display form ("... UTC (...)") — to an aware
    datetime (naive input is assumed UTC). None on failure."""
    if not ts:
        return None
    s = _FRACTION_TRIM.sub(r"\1", str(ts).strip())
    m = _DISPLAY_FORM.match(s)
    if m:
        d, hh, mm, ss = m.groups()
        return datetime.fromisoformat(f"{d}T{hh}:{mm}:{ss or '00'}+00:00")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def to_display(ts, tz_name=None):
    """Return ``"YYYY-MM-DD HH:MM UTC (H:MM AM/PM TZ)"`` — e.g.
    ``2026-07-06 15:48 UTC (11:48 AM EDT)`` — or None if *ts* is unparseable.

    UTC always comes first (24-hour); the parenthetical local wall-clock uses ``tz_name`` /
    ``FABRIC_DISPLAY_TZ`` (default America/New_York) and is omitted if the tz database is
    unavailable. Never raises.
    """
    dt = parse_iso_utc(ts)
    if dt is None:
        return None
    utc = dt.astimezone(timezone.utc)
    out = f"{utc.strftime('%Y-%m-%d %H:%M')} UTC"
    name = tz_name or os.environ.get("FABRIC_DISPLAY_TZ") or _DEFAULT_TZ
    try:
        from zoneinfo import ZoneInfo   # stdlib; needs a tz database (tzdata pkg on Windows)
        local = dt.astimezone(ZoneInfo(name))
    except Exception:
        return out   # no tz database: UTC half still renders
    hour = local.strftime("%I").lstrip("0") or "12"   # %-I is not portable to Windows
    label = local.tzname() or name
    return f"{out} ({hour}:{local.strftime('%M')} {local.strftime('%p')} {label})"


def add_display_time(record, src_key, dst_key, tz_name=None):
    """Attach ``dst_key`` = to_display(record[src_key]) to a dict when convertible. Returns record."""
    if isinstance(record, dict):
        disp = to_display(record.get(src_key), tz_name)
        if disp:
            record[dst_key] = disp
    return record
