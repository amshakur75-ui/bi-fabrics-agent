"""Staleness check for dimensional data (workspace/item lists, owner mappings).

When the agent uses dimensional data that might be stale, it discloses this so the
user knows ownership/inventory information may have changed since last collection.
"""
from datetime import datetime, timezone


# Default threshold: 24 hours for dimensional data (workspace lists, ownership mappings).
DEFAULT_THRESHOLD_HOURS = 24


def check_staleness(collected_at, *, threshold_hours=None, label="Dimensional data"):
    """Check whether collected data is stale.

    Parameters
    ----------
    collected_at : str | None
        ISO-8601 UTC timestamp of when the data was fetched (the ``collectedAt``
        field on the collector output).  ``None`` means the timestamp is absent
        (backward compat / offline mock) -- returns a gentle "freshness unknown" note.
    threshold_hours : float | None
        Hours after which the data is considered stale.  Defaults to
        ``DEFAULT_THRESHOLD_HOURS`` (24).
    label : str
        Human-readable label for the data domain (e.g. "Workspace data",
        "Ownership mappings").  Used in the note text.

    Returns
    -------
    dict
        ``{"stale": bool, "note": str | None, "ageHours": float | None}``.
        ``note`` is ``None`` when the data is fresh.
    """
    if threshold_hours is None:
        threshold_hours = DEFAULT_THRESHOLD_HOURS

    if collected_at is None:
        return {"stale": False, "note": None, "ageHours": None}

    try:
        dt = datetime.fromisoformat(str(collected_at).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return {"stale": False, "note": None, "ageHours": None}

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    age_seconds = (now - dt).total_seconds()
    age_hours = age_seconds / 3600

    if age_hours <= threshold_hours:
        return {"stale": False, "note": None, "ageHours": round(age_hours, 1)}

    # Build a human-readable age description
    if age_hours < 48:
        age_desc = f"{int(round(age_hours))} hours"
    else:
        days = age_hours / 24
        age_desc = f"{int(round(days))} days"

    note = f"{label} is {age_desc} old — ownership may have changed"
    return {"stale": True, "note": note, "ageHours": round(age_hours, 1)}


def maybe_stale_note(facts, *, threshold_hours=None, label="Workspace data"):
    """Convenience: extract ``collectedAt`` from a facts dict and return a stale-data
    note string (or ``None`` if fresh / unknown).  Designed to be called from tool
    handlers that want a one-liner staleness annotation."""
    collected_at = facts.get("collectedAt") if isinstance(facts, dict) else None
    result = check_staleness(collected_at, threshold_hours=threshold_hours, label=label)
    return result.get("note")
