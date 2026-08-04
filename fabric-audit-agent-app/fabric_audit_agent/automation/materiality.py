"""Deterministic materiality gate + escalation detection for Tier-2 alerts. Pure, no I/O, no LLM.

``classify`` decides ``report`` / ``suppress`` / ``ambiguous`` for a fired trigger; only the
ambiguous middle is handed to the LLM investigation. ``is_escalation`` decides whether an already
active incident has worsened enough to re-alert. All thresholds come from ``load_cfg`` (env
``FABRIC_TIER2_*`` overrides, defaults are the approved values) so they can be tuned without code.
"""
import os

from .incident import severity_of, primary_metric, _num

_DEFAULTS = {
    "concentration_report": 40.0,   # report at/above this share%
    "concentration_suppress": 33.0,  # barely-over-30 single blip -> suppress
    "throttle_min": 5.0,            # report throttle at/above this many minutes
    "pressure_report": 120.0,       # report peak CU% at/above this
    "pressure_suppress": 105.0,     # barely-over-100 momentary blip -> suppress
    "overage_burndown": 60.0,       # report if minutes-to-burndown below this
    "esc_share_delta": 15.0,        # escalation: share% rose by >= this
    "esc_peak_delta": 20.0,         # escalation: peak CU% rose by >= this
}


def load_cfg(env=None):
    """Load thresholds; each key overridable via FABRIC_TIER2_<KEY_UPPER>."""
    env = env if env is not None else os.environ
    cfg = dict(_DEFAULTS)
    for k in cfg:
        raw = env.get("FABRIC_TIER2_" + k.upper())
        if raw:
            try:
                cfg[k] = float(raw)
            except (TypeError, ValueError):
                pass
    return cfg


def _is_recurring(trigger):
    return bool((trigger.get("recurrence") or {}).get("isRecurring"))


def classify(trigger, cfg=None):
    """Return ``(decision, reason)`` with decision in ``report`` | ``suppress`` | ``ambiguous``.

    ``data_unavailable`` is never a capacity alert -> suppress.
    """
    cfg = cfg if cfg is not None else load_cfg()
    check = (trigger or {}).get("check")
    if check == "data_unavailable":
        return "suppress", "data-unavailable is a data gap, not a capacity incident"
    if _is_recurring(trigger):
        return "report", "recurring condition (matches prior findings)"
    if severity_of(trigger) == "warn":
        return "report", "derived severity=warn"

    if check == "concentration":
        share = _num(trigger.get("sharePct"))
        if share is not None and share >= cfg["concentration_report"]:
            return "report", f"share {share:.0f}% >= {cfg['concentration_report']:.0f}%"
        if share is not None and share < cfg["concentration_suppress"]:
            return "suppress", f"share {share:.0f}% barely over threshold, not recurring"
        return "ambiguous", "moderate concentration, not clearly material"
    if check == "throttle":
        mins = _num(trigger.get("throttleMinutes"))
        if mins is not None and mins >= cfg["throttle_min"]:
            return "report", f"throttle {mins:.0f}m >= {cfg['throttle_min']:.0f}m"
        return "ambiguous", "brief throttle signal"
    if check == "pressure":
        pct = _num(trigger.get("peakCuPct"))
        if pct is not None and pct >= cfg["pressure_report"]:
            return "report", f"peak CU {pct:.0f}% >= {cfg['pressure_report']:.0f}%"
        if pct is not None and pct < cfg["pressure_suppress"]:
            return "suppress", f"peak CU {pct:.0f}% momentary, not recurring"
        return "ambiguous", "CU pressure over 100 but not clearly sustained"
    if check == "overage":
        mtb = _num(trigger.get("minutesToBurndown"))
        if mtb is not None and mtb < cfg["overage_burndown"]:
            return "report", f"burndown in {mtb:.0f}m < {cfg['overage_burndown']:.0f}m"
        return "ambiguous", "overage accumulating, burndown not urgent"
    return "ambiguous", "unclassified trigger"


def is_escalation(trigger, prior, cfg=None):
    """True if an active incident has worsened vs its last-alerted state.

    ``prior`` = ``{"severity": str, "metric": float|None}`` from the stored alert row.
    """
    cfg = cfg if cfg is not None else load_cfg()
    ranks = {"info": 0, "warn": 1}
    if ranks.get(severity_of(trigger), 0) > ranks.get((prior or {}).get("severity"), 0):
        return True
    cur = primary_metric(trigger)
    pri = _num((prior or {}).get("metric"))
    if cur is None or pri is None:
        return False
    check = (trigger or {}).get("check")
    if check == "concentration":
        return (cur - pri) >= cfg["esc_share_delta"]
    if check == "pressure":
        return (cur - pri) >= cfg["esc_peak_delta"]
    if check == "throttle":
        return cur >= max(cfg["throttle_min"], 2 * pri)
    if check == "overage":
        return (cur - pri) >= cfg["esc_peak_delta"]
    return False
