"""Finding lifecycle. Faithful port of ``core/lifecycle.js``.

Split findings into active vs suppressed by persisted state; expired snoozes reactivate.
"""
from datetime import datetime, timezone

ACTIVE_STATES = {"open", "acknowledged"}
DEFAULT_LIFECYCLE = {"state": "open", "since": None, "snoozeUntil": None, "note": None}


def _parse_ms(s):
    """Parse an ISO timestamp to epoch-ms (mirrors JS Date.parse); None if unparseable."""
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000


def apply_lifecycle(findings, states=None, now_ms=0):
    states = states or {}
    active, suppressed = [], []
    for f in findings:
        raw = states[f["key"]] if (f.get("key") and f["key"] in states) else DEFAULT_LIFECYCLE
        lc = {**DEFAULT_LIFECYCLE, **raw}
        if now_ms > 0 and lc["state"] == "snoozed" and lc.get("snoozeUntil") is not None:
            ms = _parse_ms(lc["snoozeUntil"])
            if ms is not None and ms < now_ms:
                lc = {**lc, "state": "open", "snoozeUntil": None}
        annotated = {**f, "lifecycle": lc}
        (active if lc["state"] in ACTIVE_STATES else suppressed).append(annotated)
    return {"active": active, "suppressed": suppressed}


def set_state(states=None, key=None, state=None, opts=None):
    """Pure state transition — returns a NEW states map (does not mutate input)."""
    states = states or {}
    opts = opts or {}
    return {
        **states,
        key: {
            "state": state,
            "since": opts.get("now"),
            "snoozeUntil": opts.get("snoozeUntil"),
            "note": opts.get("note"),
        },
    }
