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


_CAPACITY_FAMILY = ("throttle", "pressure", "overage", "extreme_peak", "throttle_imminent",
                    "capacity_incident")


def incident_key(trigger):
    """Stable id for an incident, identical across runs for the same ongoing condition.

    Design A' (2026-08-09): ALL capacity-family checks (throttle / pressure / overage /
    extreme_peak / throttle_imminent / capacity_incident composite) share ONE key per
    capacity — ``capacity::<capId>`` — so multiple signal types firing for the same
    underlying event coalesce into a single alert instead of paging N times. concentration
    and cross_user stay item-scoped (per workspace/item); everything else keeps a per-check
    key.
    """
    check = (trigger or {}).get("check", "unknown")
    if check in ("concentration", "cross_user"):
        ws = trigger.get("workspace") or "?"
        item = trigger.get("item") or "?"
        return f"{check}::{ws}/{item}"
    if check in _CAPACITY_FAMILY:
        cap = trigger.get("capacityId") or "capacity"
        return f"capacity::{cap}"
    cap = trigger.get("capacityId") or "capacity"
    return f"{check}::{cap}"


def severity_of(trigger):
    """Derive ``"warn"`` | ``"info"`` from the trigger's metrics (no severity field exists)."""
    check = (trigger or {}).get("check")
    if check == "capacity_incident":
        # Composite: severity is the MAX of its component signals — one warn component wins.
        comps = trigger.get("signals") or []
        return "warn" if any(severity_of(c) == "warn" for c in comps) else "info"
    if check == "throttle":
        mins = _num(trigger.get("throttleMinutes"))
        return "warn" if mins is not None and mins >= 5 else "info"
    if check == "concentration":
        share = _num(trigger.get("sharePct"))
        return "warn" if share is not None and share >= 50 else "info"
    if check == "pressure":
        pct = _num(trigger.get("peakCuPct"))
        return "warn" if pct is not None and pct >= 120 else "info"
    if check == "extreme_peak":
        # any fired extreme_peak (>= 200% by default) is warn-severity by definition
        return "warn"
    if check == "throttle_imminent":
        worst = _num(trigger.get("worstPct"))
        return "warn" if worst is not None and worst >= 90 else "info"
    if check == "overage":
        mtb = _num(trigger.get("minutesToBurndown"))
        return "warn" if mtb is not None and mtb < 60 else "info"
    if check == "cross_user":
        n = _num(trigger.get("userCount"))
        return "warn" if n is not None and n >= 4 else "info"
    if check in ("rate_change", "silent_failure"):
        return "warn"           # a sharp climb / a blind collector both warrant attention
    # sustained (early-warning) and blind_spot (coverage note) are informational
    return "info"


def primary_metric(trigger):
    """The single numeric metric used for escalation comparison, per check type."""
    check = (trigger or {}).get("check")
    if check == "capacity_incident":
        # Composite uses peakCuPct as its primary metric — the single number that best
        # summarizes overall severity across the merged signals. Escalation logic (see
        # is_escalation) additionally compares throttleMinutes and whether a new signal
        # type joined, but the row's scalar metric is peakCuPct so it lines up with
        # pressure/extreme_peak's scale (both are % of capacity).
        return _num(trigger.get("peakCuPct"))
    if check == "concentration":
        return _num(trigger.get("sharePct"))
    if check == "throttle":
        return _num(trigger.get("throttleMinutes"))
    if check == "pressure":
        return _num(trigger.get("peakCuPct"))
    if check == "extreme_peak":
        return _num(trigger.get("peakCuPct"))
    if check == "throttle_imminent":
        return _num(trigger.get("worstPct"))
    if check == "overage":
        return _num(trigger.get("overageCumulativePct"))
    if check == "cross_user":
        return _num(trigger.get("userCount"))
    if check in ("blind_spot", "sustained"):
        return _num(trigger.get("peakCuPct"))
    if check == "rate_change":
        return _num(trigger.get("risePts"))
    if check == "silent_failure":
        return _num(trigger.get("runs"))
    return None
