"""Stable incident identity + derived severity for Tier-2 alert dedup. Pure, no I/O.

Tier-2 triggers (see ``tier2_check.py``) carry metrics + a descriptive ``normalityHint`` but
NO severity field, so severity is derived here from the metrics. ``incident_key`` is the stable
handle used to dedupe an ongoing incident across the 5-minute runs.
"""


def _num(v):
    """Parse a numeric metric, rejecting bool/None/non-numeric (returns None)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def incident_key(trigger):
    """Stable id for an incident, identical across runs for the same ongoing condition.

    concentration is item-scoped (per workspace/item); throttle/pressure/overage are
    capacity-wide (one capacity in this deployment), so they key on ``capacityId`` if the
    trigger carries one, else the literal ``capacity``.
    """
    check = (trigger or {}).get("check", "unknown")
    if check == "concentration":
        ws = trigger.get("workspace") or "?"
        item = trigger.get("item") or "?"
        return f"concentration::{ws}/{item}"
    cap = trigger.get("capacityId") or "capacity"
    return f"{check}::{cap}"


def severity_of(trigger):
    """Derive ``"warn"`` | ``"info"`` from the trigger's metrics (no severity field exists)."""
    check = (trigger or {}).get("check")
    if check == "throttle":
        mins = _num(trigger.get("throttleMinutes"))
        return "warn" if mins is not None and mins >= 5 else "info"
    if check == "concentration":
        share = _num(trigger.get("sharePct"))
        return "warn" if share is not None and share >= 50 else "info"
    if check == "pressure":
        pct = _num(trigger.get("peakCuPct"))
        return "warn" if pct is not None and pct >= 120 else "info"
    if check == "overage":
        mtb = _num(trigger.get("minutesToBurndown"))
        return "warn" if mtb is not None and mtb < 60 else "info"
    return "info"


def primary_metric(trigger):
    """The single numeric metric used for escalation comparison, per check type."""
    check = (trigger or {}).get("check")
    if check == "concentration":
        return _num(trigger.get("sharePct"))
    if check == "throttle":
        return _num(trigger.get("throttleMinutes"))
    if check == "pressure":
        return _num(trigger.get("peakCuPct"))
    if check == "overage":
        return _num(trigger.get("overageCumulativePct"))
    return None
