"""Human-readable local-time display for raw UTC telemetry timestamps. Stdlib only.

Telemetry timestamps are ISO-8601 UTC (e.g. ``2026-07-06T15:48:00.0000000Z``). The tools keep
those raw values for machine use and attach a display twin (``tsLocal``/``whenLocal``/...) built
here, so the agent presents wall-clock time without doing its own timezone math — LLM DST
arithmetic is exactly the kind of silent numeric error the honesty rules exist to prevent.

Timezone comes from ``FABRIC_DISPLAY_TZ`` (IANA name; default ``America/New_York``). Conversion
uses ``zoneinfo`` so EDT/EST daylight-saving transitions are correct; the label is the zone's own
abbreviation (``EDT``/``EST``). If the timestamp can't be parsed or the tz database is missing,
returns None and callers simply omit the display field — the raw UTC value is always still there.
"""
import os
import re
from datetime import datetime, timezone

_DEFAULT_TZ = "America/New_York"

# fromisoformat() (3.11+) tolerates most ISO-8601, but not >6 fractional-second digits —
# Log Analytics emits 7 (e.g. ".3079171Z"). Trim to microseconds before parsing.
_FRACTION_TRIM = re.compile(r"(\.\d{6})\d+")


def parse_iso_utc(ts):
    """Parse an ISO-8601 string to an aware datetime (naive input is assumed UTC). None on failure."""
    if not ts:
        return None
    s = _FRACTION_TRIM.sub(r"\1", str(ts).strip())
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def to_display(ts, tz_name=None):
    """Return ``"YYYY-MM-DD H:MM AM/PM TZ"`` (e.g. ``2026-07-06 11:48 AM EDT``) or None.

    ``tz_name`` falls back to ``FABRIC_DISPLAY_TZ``, then America/New_York. Never raises —
    an unparseable timestamp or missing tz database yields None so callers can omit the field.
    """
    dt = parse_iso_utc(ts)
    if dt is None:
        return None
    name = tz_name or os.environ.get("FABRIC_DISPLAY_TZ") or _DEFAULT_TZ
    try:
        from zoneinfo import ZoneInfo   # stdlib; needs a tz database (tzdata pkg on Windows)
        local = dt.astimezone(ZoneInfo(name))
    except Exception:
        return None
    hour = local.strftime("%I").lstrip("0") or "12"   # %-I is not portable to Windows
    label = local.tzname() or name
    return f"{local.strftime('%Y-%m-%d')} {hour}:{local.strftime('%M')} {local.strftime('%p')} {label}"


def add_display_time(record, src_key, dst_key, tz_name=None):
    """Attach ``dst_key`` = to_display(record[src_key]) to a dict when convertible. Returns record."""
    if isinstance(record, dict):
        disp = to_display(record.get(src_key), tz_name)
        if disp:
            record[dst_key] = disp
    return record
