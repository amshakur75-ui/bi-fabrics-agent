"""Time-window resolution for event/capacity KQL queries — sub-day and absolute windows.

``resolve_window(days=None, hours=None, start=None, end=None) -> {"clause", "label"}`` builds
the ``TimeGenerated`` WHERE clause a collector's ``_kql`` builder splices in verbatim, plus a
short human-readable label the tool envelopes echo back (so a caller can see what window was
actually queried).

Precedence (highest first):
  1. ``start`` AND ``end`` (both required together) -> an absolute ``between (...)`` clause.
  2. ``hours`` (may be fractional, e.g. 0.25 = last 15 min) -> ``ago(<hours>h)``.
  3. ``days`` -> ``ago(<days>d)``.
  4. default -> ``ago(30d)``.

Nullish semantics throughout: ``None`` means "unset" and falls through to the next tier;
``0`` is a valid, meaningful value (a 0-day/0-hour window) and does NOT fall through.

Python 3.10's ``datetime.fromisoformat`` does NOT accept a trailing "Z" (that landed in 3.11) --
inputs are normalized ("Z" -> "+00:00") BEFORE parsing so this works on 3.10, the package's
stated minimum. Parsed timestamps are converted to UTC and re-emitted with a literal "Z" suffix
(never "+00:00") in the KQL, since that's the ``datetime(...)`` literal form KQL expects.
"""
from datetime import datetime, timezone


def _parse_iso_utc(value, field_name):
    """Parse an ISO-8601 string (optionally "Z"-suffixed) to a UTC ``datetime``.

    Raises ``ValueError`` with a message naming *field_name* on any malformed input --
    callers (tool handlers) catch this and return an error envelope rather than crashing.
    """
    try:
        normalized = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"malformed ISO timestamp for '{field_name}': {value!r}") from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _kql_datetime_literal(dt):
    """Render a UTC ``datetime`` as the ``<ISO>Z`` literal KQL's ``datetime(...)`` expects."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def resolve_window(days=None, hours=None, start=None, end=None):
    """Return ``{"clause": <KQL WHERE clause>, "label": <human string>}`` per the precedence
    documented on the module. Raises ``ValueError`` on a malformed ``start``/``end``."""
    if start is not None and end is not None:
        start_dt = _parse_iso_utc(start, "start")
        end_dt = _parse_iso_utc(end, "end")
        start_lit = _kql_datetime_literal(start_dt)
        end_lit = _kql_datetime_literal(end_dt)
        clause = f"| where TimeGenerated between (datetime({start_lit}) .. datetime({end_lit}))"
        start_date = start_lit[:10]
        end_date = end_lit[:10]
        start_time = start_lit[11:]
        end_time = end_lit[11:]
        if start_date == end_date:
            label = f"{start_date}T{start_time}..{end_time}"
        else:
            label = f"{start_lit}..{end_lit}"
        return {"clause": clause, "label": label}

    if hours is not None:
        clause = f"| where TimeGenerated > ago({hours}h)"
        if hours < 1:
            minutes = hours * 60
            minutes_str = f"{minutes:g}"
            label = f"last {minutes_str}min"
        else:
            hours_str = f"{hours:g}"
            label = f"last {hours_str}h"
        return {"clause": clause, "label": label}

    if days is not None:
        clause = f"| where TimeGenerated > ago({days}d)"
        return {"clause": clause, "label": f"last {days}d"}

    return {"clause": "| where TimeGenerated > ago(30d)", "label": "last 30d"}
