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
        # Bar comes from materiality.load_cfg so severity and materiality can never disagree.
        # A hardcoded 5 here meant classify() reported at 3.0 minutes while severity stayed
        # `info` — and because is_escalation's first rule is a severity-RANK comparison, that
        # also silently changed which worsenings could break through. The value is window-scoped
        # (5-min sweep => 5.0 is the max observable), see materiality._DEFAULTS["throttle_min"].
        mins = _num(trigger.get("throttleMinutes"))
        if mins is None:
            return "info"
        from .materiality import load_cfg as _load_cfg
        return "warn" if mins >= float(_load_cfg()["throttle_min"]) else "info"
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


# Severity RANK within the capacity family, worst first. Used by ``is_escalation`` so that a
# signal JOINING an incident only counts as a worsening when it is at least as severe as the worst
# signal already recorded. Without this, a weaker signal arriving a sweep later — very common,
# because throttle_imminent and pressure are derived from the SAME capacity dict and land in
# different windows — produced a spurious second Teams card for an incident that had not worsened.
#   throttle / extreme_peak : actual throttling, or a >=200% spike       -> worst
#   pressure                : CU already over 100%
#   overage / throttle_imminent : accumulating burndown, or 80% of a Fabric threshold (a warning
#                             that nothing has breached yet)             -> least
_SIGNAL_RANK = {
    "throttle": 3, "extreme_peak": 3,
    "pressure": 2,
    "overage": 1, "throttle_imminent": 1,
}


def signal_rank(name):
    """Severity rank of one capacity signal (higher = worse). Unknown names rank 0."""
    return _SIGNAL_RANK.get(name, 0)


def signal_set(trigger):
    """The sorted set of capacity signals a trigger represents, as a list.

    A composite carries its merged ``signalTypes``; a lone capacity trigger is a set of one
    (itself). Design A' shares ONE incident key across the whole capacity family, so this is
    what lets ``is_escalation`` answer "did a new signal join this incident?" uniformly —
    including the single-signal -> multi-signal transition (e.g. pressure crossing into
    throttle), which is a genuine worsening even when the metrics barely move.
    """
    check = (trigger or {}).get("check")
    if check == "capacity_incident":
        return sorted(trigger.get("signalTypes") or [])
    return [check] if check in _CAPACITY_FAMILY else []


def primary_metric(trigger):
    """The single numeric metric used for escalation comparison, per check type.

    UNIT SAFETY (Design A'): every capacity-family check reports ``peakCuPct`` so the stored
    ``metric`` has ONE stable unit across the shared ``capacity::<id>`` key. Before this,
    throttle stored minutes while pressure stored percent, so a tick that switched check type
    compared minutes against percent — which both invented escalations (throttle 2.0 -> pressure
    110 read as "+108 points") and swallowed real ones (a 30-minute throttle after a 250% peak
    failed ``30 >= 2*250`` and went silent). Throttle severity is still tracked, on its own axis,
    via the row's ``throttleMinutes``.
    """
    check = (trigger or {}).get("check")
    if check in _CAPACITY_FAMILY:
        # This branch covers throttle / pressure / overage / extreme_peak / throttle_imminent /
        # capacity_incident. Do NOT add per-check branches for those below — they would be
        # unreachable, and a stale one (e.g. overage returning overageCumulativePct) reads as if
        # the unit-safety fix were incomplete when it is simply shadowed.
        return _num(trigger.get("peakCuPct"))
    if check == "concentration":
        return _num(trigger.get("sharePct"))
    if check == "cross_user":
        return _num(trigger.get("userCount"))
    if check in ("blind_spot", "sustained"):
        return _num(trigger.get("peakCuPct"))
    if check == "rate_change":
        return _num(trigger.get("risePts"))
    if check == "silent_failure":
        return _num(trigger.get("runs"))
    return None
