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
    # Step 2 gates (env-overridable, tune once running):
    "cross_user_min_users": 3.0,    # same-item cross-user: >= this many qualifying users
    "cross_user_share": 15.0,       # ...each driving >= this % of the item's activity
    "blind_spot_cu": 70.0,          # coverage gap: true CU% >= this with zero monitored activity
    # Stateful gates (read the tier2_readings rolling history):
    "sustained_band_low": 70.0,     # sustained early-warning band lower bound (true CU%)
    "sustained_band_high": 90.0,    # ...upper bound (above this it's pressure, a harder alert)
    "sustained_min_minutes": 20.0,  # ...held for >= this many consecutive minutes
    "roc_delta": 15.0,              # rate-of-change: CU% rose >= this many points in one 5-min window
    "silent_fail_runs": 3.0,        # silent-failure: collector blind for >= this many runs
    "hysteresis_ticks": 3.0,        # attribution must persist >= this many consecutive checks before it alerts
    # Design A' (Aug 2026) — additional capacity signals beyond throttle/pressure/overage:
    "extreme_peak_pct": 200.0,      # single-window CU peak at/above this = report even w/o throttle
    "throttle_imminent_pct": 80.0,  # Fabric threshold pct at/above this = early-warning alert
    # Design A' quiet-to-resolve: a capacity incident holds open through brief clear-and-return
    # gaps rather than resolving on the first absent tick. Auto-resolve fires only after
    # ``quiet_ticks`` CONSECUTIVE absences (default 12 x 5-min sweep = 60 minutes clean). A
    # re-fire while in the grace period is treated as the SAME incident (no new card, escalation
    # rules still apply). Kills the resolve-then-re-fire flap that appeared on Aug 5 when the
    # same event bounced across two adjacent sweep windows.
    "quiet_ticks": 12.0,
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
    if check == "capacity_incident":
        # Composite: fires only when 2+ capacity family signals coalesce, so a fired
        # composite is always material. Reason names the signals seen.
        sigs = trigger.get("signalTypes") or []
        return "report", f"multi-signal capacity incident ({', '.join(sigs)})" if sigs \
            else "multi-signal capacity incident"
    if _is_recurring(trigger):
        return "report", "recurring condition (matches prior findings)"
    if severity_of(trigger) == "warn":
        return "report", "derived severity=warn"

    # Anti-flapping for attribution (concentration / same-item cross-user) is enforced by HYSTERESIS
    # in process_alerts (a signal must persist N consecutive 5-min checks before it alerts), NOT by a
    # severity floor here. An earlier build suppressed ALL Info-level attribution outright, which
    # silenced the product's core concentration / cross-user alerts entirely — because live
    # attribution is almost always Info-severity — so that floor was removed. The materiality gate
    # below decides whether a fired signal is worth surfacing; hysteresis decides whether it has
    # lasted long enough to be real (a flapping/rotating signal never reaches the streak).
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
    if check == "extreme_peak":
        peak = _num(trigger.get("peakCuPct"))
        # detector only fires when peak >= threshold, so a fired trigger is always material
        return "report", f"extreme CU peak {peak:.0f}%" if peak else "extreme CU peak"
    if check == "throttle_imminent":
        worst = _num(trigger.get("worstPct"))
        # detector only fires when >= threshold, so a fired trigger is always material
        return "report", f"Fabric throttle threshold at {worst:.0f}%" if worst else "throttle imminent"
    if check == "cross_user":
        # already gated on >= N users each >= X% share, so a fired trigger is material
        n = _num(trigger.get("userCount"))
        reason = (f"{n:.0f} users each driving a large share of one item" if n
                  else "multiple users driving one item")
        return "report", reason
    if check == "blind_spot":
        # only fires when true CU% is high with zero attribution — always worth surfacing
        return "report", "high true CU% with no monitored activity (coverage gap)"
    if check == "sustained":
        return "report", "CU% sustained in the early-warning band"
    if check == "rate_change":
        return "report", f"CU% climbing +{_num(trigger.get('risePts')) or 0:.0f} pts/window"
    if check == "silent_failure":
        return "report", "collector blind for consecutive runs"
    return "ambiguous", "unclassified trigger"


def is_escalation(trigger, prior, cfg=None):
    """True if an active incident has worsened vs its last-alerted state.

    ``prior`` = ``{"severity": str, "metric": float|None, "signalTypes": list|None}`` from
    the stored alert row.
    """
    cfg = cfg if cfg is not None else load_cfg()
    ranks = {"info": 0, "warn": 1}
    if ranks.get(severity_of(trigger), 0) > ranks.get((prior or {}).get("severity"), 0):
        return True
    check = (trigger or {}).get("check")
    if check == "capacity_incident":
        # Composite escalates if any of: peak worsened, throttle worsened, a new signal type
        # joined the incident vs the prior stored composite. New signal joining is a real
        # worsening even when metrics didn't move — e.g. pressure crosses into throttle.
        cur = _num(trigger.get("peakCuPct"))
        pri = _num((prior or {}).get("metric"))
        if cur is not None and pri is not None and (cur - pri) >= cfg["esc_peak_delta"]:
            return True
        cur_thr = _num(trigger.get("throttleMinutes"))
        pri_thr = _num((prior or {}).get("throttleMinutes"))
        if cur_thr is not None and pri_thr is not None and cur_thr >= max(
                cfg["throttle_min"], 2 * pri_thr):
            return True
        cur_sigs = set(trigger.get("signalTypes") or [])
        pri_sigs = set((prior or {}).get("signalTypes") or [])
        if cur_sigs - pri_sigs:
            return True
        return False
    cur = primary_metric(trigger)
    pri = _num((prior or {}).get("metric"))
    if cur is None or pri is None:
        return False
    if check == "concentration":
        return (cur - pri) >= cfg["esc_share_delta"]
    if check == "pressure":
        return (cur - pri) >= cfg["esc_peak_delta"]
    if check == "throttle":
        return cur >= max(cfg["throttle_min"], 2 * pri)
    if check == "overage":
        return (cur - pri) >= cfg["esc_peak_delta"]
    if check == "extreme_peak":
        return (cur - pri) >= cfg["esc_peak_delta"]
    if check == "throttle_imminent":
        return (cur - pri) >= cfg["esc_peak_delta"]
    return False
