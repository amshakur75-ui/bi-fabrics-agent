"""Tier 2 cheap deterministic check — runs every 5 minutes, NO LLM calls (Phase 9).

Pulls live collectors (at minimum Capacity Events — the source that carries both throttle/CU%
and concentration signal) and runs ONLY the deterministic gate checks from ``gates.py``. When
something trips, triggers an immediate alert with the raw trigger data. The next scheduled full
sweep (Tier 1, daily) picks up the full LLM-reasoned narrative — Tier 2 never calls ``run_audit``
or the reasoner. Pure and injectable (same DI pattern as ``job.run_job``).

Priority order of checks:
  1. ``concentration_gate()``  — 30% single-user/item concentration (PRIMARY)
  2. ``throttle_claim_gate()`` — confirmed throttle signal (PRIMARY)
  3. ``pressure_claim_gate()`` — CU% > 100 without a throttle signal
  4. Overage check            — nonzero ``overageTotalMs`` (burndown is accumulating)
  5. Any STOP gate in ``gates.py`` tripping (``null_data_gate`` inconclusive)

Read-only absolute — this module surfaces findings, never writes/scales/refreshes.
"""
import math
import urllib.parse
from datetime import datetime, timezone

from ..investigation.gates import (
    concentration_gate,
    throttle_claim_gate,
    pressure_claim_gate,
    null_data_gate,
)
from .incident import incident_key, severity_of, primary_metric
from .materiality import classify, is_escalation, load_cfg
from ..timefmt import to_display


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _check_concentration(facts, config=None):
    """Check concentration gate against items in the collected facts.

    Returns a list of trigger dicts for each item that passes the concentration gate.
    """
    triggers = []
    items = (facts or {}).get("items") or []
    threshold = None
    if config:
        threshold = float((config.get("capacity") or {}).get("concentrationPct", 30))
    # Capacity this window (Part 5): carried as a SEPARATE fact alongside the attribution share —
    # never computed from it. Omitted (not fabricated) when this run's collector had no capacity
    # reading at all.
    cap = (facts or {}).get("capacity") or {}
    cap_peak = cap.get("peakCuPct")
    cap_throttle = cap.get("throttleMinutes")
    for item in items:
        share = item.get("sharePct")
        if share is None:
            continue
        try:
            share = float(share)
        except (TypeError, ValueError):
            continue
        gate_args = {"share_pct": share}
        if threshold is not None:
            gate_args["threshold"] = threshold
        result = concentration_gate(**gate_args)
        if result["passed"]:
            hint = ("High share — likely automated/scheduled or runaway process; "
                    "verify if this is a known batch job" if share >= 50
                    else "Moderate share — may be a large legitimate user run; "
                         "check if this matches a known scheduled job or report")
            trig = {
                "check": "concentration",
                "gate": result,
                "item": item.get("name"),
                "workspace": item.get("workspace"),
                "sharePct": share,
                "owner": item.get("owner"),
                "topUsers": item.get("topUsers"),
                "normalityHint": hint,
            }
            if cap_peak is not None:
                trig["capacityPeakCuPct"] = cap_peak
                trig["capacityThrottleMinutes"] = cap_throttle
            triggers.append(trig)
    return triggers


def _check_throttle(facts):
    """Check throttle gate against capacity data.

    Returns a list of trigger dicts (at most one — throttle is capacity-wide).
    """
    cap = (facts or {}).get("capacity") or {}
    result = throttle_claim_gate(cap)
    if result["passed"]:
        trig = {"check": "throttle", "gate": result,
                "throttleMinutes": cap.get("throttleMinutes"),
                "peakCuPct": cap.get("peakCuPct"),
                "normalityHint": "Capacity exceeded its throttle threshold — check if this "
                                 "coincides with a scheduled refresh or batch window"}
        # B3 correlation booster: propagate peakAt onto the trigger so the correlator anchors
        # user spikes against the actual capacity event time, not the sweep's run_at.
        if cap.get("peakAt"):
            trig["peakAt"] = cap["peakAt"]
        if cap.get("capacityId"):
            trig["capacityId"] = cap["capacityId"]
        return [trig]
    return []


def _check_pressure(facts):
    """Check CU-pressure gate (peakCuPct > 100, even without a throttle signal)."""
    cap = (facts or {}).get("capacity") or {}
    result = pressure_claim_gate(cap)
    if result["passed"]:
        trig = {"check": "pressure", "gate": result,
                "peakCuPct": cap.get("peakCuPct"),
                "normalityHint": "CU exceeded 100% but throttle not yet confirmed — watch "
                                 "for escalation in the next few checks"}
        # B3 correlation booster: propagate peakAt for anchor.
        if cap.get("peakAt"):
            trig["peakAt"] = cap["peakAt"]
        if cap.get("capacityId"):
            trig["capacityId"] = cap["capacityId"]
        return [trig]
    return []


def _check_extreme_peak(facts, mcfg=None):
    """Design A' — a single window peaking >= extreme_peak_pct (default 200%) is worth an alert
    on its own, even if the smoothing absorbed it and no throttle actually fired. Catches the
    "huge query hits 300% for one window but Fabric ate it" case — Fabric got lucky this time,
    but the load was real."""
    from .materiality import load_cfg
    mcfg = mcfg if mcfg is not None else load_cfg()
    threshold = float(mcfg.get("extreme_peak_pct", 200.0))
    cap = (facts or {}).get("capacity") or {}
    try:
        peak = float(cap.get("peakCuPct"))
    except (TypeError, ValueError):
        return []
    if peak < threshold:
        return []
    trig = {"check": "extreme_peak", "peakCuPct": round(peak, 1),
            "extremeThreshold": threshold,
            "normalityHint": (f"CU peaked at {peak:.0f}% (>= {threshold:.0f}%) — a very large spike; "
                              "Fabric smoothing may have absorbed it this time, but the underlying "
                              "load was real and could throttle on a smaller capacity or a longer "
                              "sustained window.")}
    if cap.get("peakAt"):
        trig["peakAt"] = cap["peakAt"]
    if cap.get("capacityId"):
        trig["capacityId"] = cap["capacityId"]
    return [trig]


def _check_throttle_imminent(facts, mcfg=None):
    """Design A' — Fabric's own throttle-threshold pcts (interactiveDelay/interactiveRejection/
    backgroundRejection) sitting at or above throttle_imminent_pct (default 80%) means throttling
    is approaching but has not fired yet. An early-warning that lets a human intervene BEFORE
    users see slow queries/refresh delays — Fabric's own signal that headroom is exhausting."""
    from .materiality import load_cfg
    mcfg = mcfg if mcfg is not None else load_cfg()
    threshold = float(mcfg.get("throttle_imminent_pct", 80.0))
    cap = (facts or {}).get("capacity") or {}
    pcts = {"interactiveDelay": cap.get("maxInteractiveDelayPct"),
            "interactiveRejection": cap.get("maxInteractiveRejectionPct"),
            "background": cap.get("maxBackgroundRejectionPct")}
    breached = {k: v for k, v in pcts.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= threshold}
    if not breached:
        return []
    worst = max(breached.values())
    label = ", ".join(f"{k}={round(v, 1)}%" for k, v in breached.items())
    trig = {"check": "throttle_imminent", "worstPct": round(worst, 1),
            "imminentThreshold": threshold, "thresholdPcts": {k: round(v, 2) for k, v in breached.items()},
            "peakCuPct": cap.get("peakCuPct"),
            "normalityHint": (f"Fabric throttle threshold(s) at {label} — approaching but not yet "
                              "throttling. Investigate the current load before Fabric starts "
                              "delaying interactive queries or rejecting refreshes.")}
    if cap.get("peakAt"):
        trig["peakAt"] = cap["peakAt"]
    if cap.get("capacityId"):
        trig["capacityId"] = cap["capacityId"]
    return [trig]


def _check_overage(facts):
    """Check for nonzero overage (burndown accumulating).

    Looks at capacity-level ``overageTotalMs`` (set by A2 extraction).
    """
    cap = (facts or {}).get("capacity") or {}
    overage = cap.get("overageTotalMs")
    if overage is not None:
        try:
            overage = float(overage)
        except (TypeError, ValueError):
            return []
        if overage > 0:
            return [{"check": "overage", "overageTotalMs": overage,
                     "overageCumulativePct": cap.get("overageCumulativePct"),
                     "minutesToBurndown": cap.get("minutesToBurndown"),
                     "normalityHint": "Overage is accumulating — if this is a one-off large "
                                      "job it will burn down; if it persists across multiple "
                                      "checks it's a pattern"}]
    return []


def _check_same_item_cross_user(facts, mcfg=None):
    """Same-item cross-user pattern (Step 2, PROXY): >= N distinct users EACH driving >= X% of one
    item's monitored activity. A broadly-hit item (shared/popular report), NOT a single-user runaway
    — the fix is the item's design, not one person. Uses per-user cuSeconds share within the item."""
    from .materiality import load_cfg
    mcfg = mcfg if mcfg is not None else load_cfg()
    min_users = int(mcfg.get("cross_user_min_users", 3))
    share_each = float(mcfg.get("cross_user_share", 15.0))
    # Capacity this window (Part 5): SEPARATE fact, never derived from the attribution share; omitted
    # when this run's collector had no capacity reading at all.
    cap = (facts or {}).get("capacity") or {}
    cap_peak = cap.get("peakCuPct")
    cap_throttle = cap.get("throttleMinutes")
    triggers = []
    for it in (facts or {}).get("items") or []:
        try:
            item_cu = float(it.get("cuSeconds"))
        except (TypeError, ValueError):
            continue
        top = it.get("topUsers") or []
        if item_cu <= 0 or len(top) < min_users:
            continue
        qualifying = []
        for u in top:
            try:
                ucu = float(u.get("cuSeconds"))
            except (TypeError, ValueError):
                continue
            if ucu / item_cu * 100 >= share_each:
                qualifying.append(u.get("user"))
        if len(qualifying) >= min_users:
            trig = {
                "check": "cross_user", "item": it.get("name"), "workspace": it.get("workspace"),
                "userCount": len(qualifying), "users": qualifying,
                "sharePct": round(float(it.get("sharePct") or 0), 1),
                "normalityHint": (f"{len(qualifying)} users are each driving a large share of this one "
                                  "item — a shared/popular item (e.g. a broadly-used report), not a "
                                  "single-user runaway; look at the item's design, not one person."),
            }
            if cap_peak is not None:
                trig["capacityPeakCuPct"] = cap_peak
                trig["capacityThrottleMinutes"] = cap_throttle
            triggers.append(trig)
    return triggers


def _check_cross_source_blind_spot(facts, mcfg=None):
    """Cross-source consistency / blind-spot (Step 2, META): true CU% is high but monitored activity
    came back empty — we can see the load but not WHO. Distinct from a quiet window (low CU) and from
    a concentration alert. Flags a visibility/coverage gap, not a capacity emergency."""
    from .materiality import load_cfg
    mcfg = mcfg if mcfg is not None else load_cfg()
    cap = (facts or {}).get("capacity") or {}
    items = (facts or {}).get("items") or []
    try:
        peak = float(cap.get("peakCuPct"))
    except (TypeError, ValueError):
        return []
    threshold = float(mcfg.get("blind_spot_cu", 70.0))
    if peak >= threshold and not items:
        return [{"check": "blind_spot", "peakCuPct": round(peak, 1),
                 "normalityHint": ("Capacity shows real load but no monitored activity came back this "
                                   "window — attribution (Log Analytics) may be lagging, filtered, or "
                                   "unconfigured, so WHO is driving this load is not visible.")}]
    return []


# ---- Stateful gates (Step 2): reason across the last N readings (tier2_readings store) ----

def _peak(reading):
    try:
        return float(reading.get("peakCuPct"))
    except (TypeError, ValueError):
        return None


def _check_sustained_band(readings, mcfg=None):
    """Sustained-but-under-threshold (Step 2, TRUE-CU): CU% held inside the [low, high] band for
    >= min_minutes of consecutive 5-min windows. An early-warning that pressure is building — NOT a
    hard alert (it never crosses 100%). Reads newest-first ``readings``."""
    from .materiality import load_cfg
    mcfg = mcfg if mcfg is not None else load_cfg()
    low = float(mcfg.get("sustained_band_low", 70.0))
    high = float(mcfg.get("sustained_band_high", 90.0))
    k = max(2, int(math.ceil(float(mcfg.get("sustained_min_minutes", 20.0)) / 5.0)))
    if len(readings) < k:
        return []
    vals = [_peak(r) for r in readings[:k]]
    if any(v is None for v in vals):
        return []
    if all(low <= v <= high for v in vals):
        return [{"check": "sustained", "peakCuPct": round(vals[0], 1), "minutesInBand": k * 5,
                 "bandLow": low, "bandHigh": high,
                 "normalityHint": (f"CU% has sat in the {low:.0f}-{high:.0f}% band for {k * 5}+ "
                                   "minutes — no throttle yet, but headroom is thinning; watch for a "
                                   "crossing.")}]
    return []


def _check_rate_of_change(readings, mcfg=None):
    """Rate-of-change (Step 2, TRUE-CU): CU% jumped by >= roc_delta points between the last two
    windows, even if still under 100% — a sharp climb worth flagging before it throttles."""
    from .materiality import load_cfg
    mcfg = mcfg if mcfg is not None else load_cfg()
    delta = float(mcfg.get("roc_delta", 15.0))
    if len(readings) < 2:
        return []
    cur, prev = _peak(readings[0]), _peak(readings[1])
    if cur is None or prev is None:
        return []
    rise = cur - prev
    if rise >= delta:
        return [{"check": "rate_change", "peakCuPct": round(cur, 1), "prevCuPct": round(prev, 1),
                 "risePts": round(rise, 1),
                 "normalityHint": (f"CU% climbed {rise:.0f} points in 5 minutes ({prev:.0f}% -> "
                                   f"{cur:.0f}%) — a sharp rise; if the trend holds it may cross 100% "
                                   "soon.")}]
    return []


def _check_silent_failure(readings, mcfg=None):
    """Silent-failure / stale-data (Step 2, META): the collector returned no data (error or empty)
    for N consecutive runs — the agent has a visibility gap and may be silently blind."""
    from .materiality import load_cfg
    mcfg = mcfg if mcfg is not None else load_cfg()
    n = int(mcfg.get("silent_fail_runs", 3))
    if len(readings) < n:
        return []
    if all(r.get("collectorOk") is False for r in readings[:n]):
        return [{"check": "silent_failure", "runs": n,
                 "normalityHint": (f"The collector returned no usable data for {n} runs in a row — "
                                   "the source may be down, unauthorized, or misconfigured; alerts "
                                   "cannot be trusted until this clears.")}]
    return []


def _check_data_availability(facts):
    """Check for null/inconclusive data (STOP gate)."""
    result = null_data_gate(facts)
    if not result["conclusive"]:
        return [{"check": "data_unavailable", "gate": result}]
    return []


def _cross_reference_recurrence(triggers, findings_store, scope=None, tenant=None):
    """Cross-reference triggers against recent audit_findings for recurrence detection.

    When ``findings_store`` is available (Phase 6 Delta table), queries recent findings
    for the same scope and annotates triggers with recurrence info. A missing or failing
    store never blocks — this is enrichment only.
    """
    if findings_store is None or not triggers:
        return triggers
    try:
        from ..context_findings import query_recent_findings
        recent = query_recent_findings(findings_store, scope=scope, tenant=tenant, limit=10)
        if not recent:
            return triggers
        recent_keys = {f.get("findingKey") for f in recent if f.get("findingKey")}
        for t in triggers:
            check = t.get("check", "")
            # Map Tier 2 check names to finding key prefixes used in the full sweep
            key_prefixes = {
                "concentration": "capacity.concentration",
                "cross_user": "capacity.concentration",
                "throttle": "capacity.throttle",
                "pressure": "capacity.pressure",
                "extreme_peak": "capacity.pressure",
                "throttle_imminent": "capacity.throttle",
                "overage": "capacity.overage",
            }
            prefix = key_prefixes.get(check)
            if not prefix:  # unknown/meta check (e.g. blind_spot) -> never "matches all keys"
                t["recurrence"] = {"isRecurring": False}
                continue
            matching = [k for k in recent_keys if k and k.startswith(prefix)]
            if matching:
                t["recurrence"] = {
                    "isRecurring": True,
                    "matchingFindings": sorted(matching),
                    "note": (f"This {check} trigger matches {len(matching)} recent finding(s) "
                             "from prior sweeps — likely a recurring condition, not a fresh event."),
                }
            else:
                t["recurrence"] = {"isRecurring": False}
    except Exception:
        pass
    return triggers


def _build_tier2_alert_summary(triggers):
    """Build a human-readable summary for the alert payload."""
    if not triggers:
        return "Tier 2 check: no triggers fired."
    parts = []
    for t in triggers:
        check = t.get("check", "unknown")
        if check == "concentration":
            parts.append(f"Concentration: {t.get('item', '?')} at {t.get('sharePct', '?')}%")
        elif check == "throttle":
            parts.append(f"Throttling: {t.get('throttleMinutes', '?')} min")
        elif check == "pressure":
            parts.append(f"CU pressure: peak {t.get('peakCuPct', '?')}%")
        elif check == "extreme_peak":
            parts.append(f"Extreme peak: {t.get('peakCuPct', '?')}%")
        elif check == "throttle_imminent":
            parts.append(f"Throttle imminent: {t.get('worstPct', '?')}% threshold")
        elif check == "overage":
            parts.append(f"Overage: {t.get('overageTotalMs', '?')} ms cumulative")
        elif check == "data_unavailable":
            parts.append("Data unavailable (inconclusive)")
        else:
            parts.append(f"{check} triggered")
    recurring = [t for t in triggers if (t.get("recurrence") or {}).get("isRecurring")]
    recurrence_note = ""
    if recurring:
        recurrence_note = f" ({len(recurring)} recurring)"
    return f"Tier 2 alert: {'; '.join(parts)}{recurrence_note}"


# ---- Alerting orchestration (sub-project #2): dedup/materiality FIRST, LLM only when alerting ----

def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _ack_suppressed(ack_store, chat_id, now_dt):
    """True if a human acked (permanently) or snoozed (until a future time) this incident's chat.

    ``ack_store`` is ``{"get": fn(chat_id) -> {"status","snoozeUntil"} | None}`` (Lakebase-backed in
    prod). Missing store / chat / record, or any error, means NOT suppressed (fail-open — never
    silently swallow a real alert)."""
    if ack_store is None or not chat_id:
        return False
    try:
        rec = ack_store["get"](chat_id)
    except Exception:
        return False
    if not rec:
        return False
    status = (rec.get("status") or "").lower()
    if status in ("acked", "resolved"):
        # Acked, or a human-resolved ticket (Step 8/9) — hold reminders. (If the condition later
        # goes absent and RECURS, the app's Reopen clears this so reminders + alerts resume.)
        return True
    if status == "snoozed":
        until = _parse_iso(rec.get("snoozeUntil"))
        return until is not None and now_dt < until
    return False


_SIGNAL_SHORT = {"throttle": "throttling", "pressure": "CU pressure", "overage": "overage",
                 "extreme_peak": "extreme peak", "throttle_imminent": "throttle imminent"}


def _title_for(t):
    check = t.get("check")
    if check == "capacity_incident":
        parts = [_SIGNAL_SHORT.get(s, s) for s in (t.get("signalTypes") or [])]
        peak = t.get("peakCuPct")
        peak_note = f" — peak {peak}%" if peak is not None else ""
        return f"Capacity incident ({' + '.join(parts)}){peak_note}"
    if check == "concentration":
        return f"Concentration: {t.get('item', '?')} at {t.get('sharePct', '?')}%"
    if check == "throttle":
        return f"Throttling on capacity ({t.get('throttleMinutes', '?')} min)"
    if check == "pressure":
        return f"CU pressure: peak {t.get('peakCuPct', '?')}%"
    if check == "extreme_peak":
        return f"Extreme CU peak: {t.get('peakCuPct', '?')}%"
    if check == "throttle_imminent":
        return f"Throttle imminent: threshold at {t.get('worstPct', '?')}%"
    if check == "overage":
        return "Capacity overage accumulating"
    if check == "cross_user":
        return f"Cross-user load: {t.get('item', '?')} ({t.get('userCount', '?')} users)"
    if check == "blind_spot":
        return f"Coverage gap: CU {t.get('peakCuPct', '?')}% but no attribution"
    if check == "sustained":
        return f"Sustained CU {t.get('peakCuPct', '?')}% ({t.get('minutesInBand', '?')}m in band)"
    if check == "rate_change":
        return f"CU climbing fast: {t.get('prevCuPct', '?')}% → {t.get('peakCuPct', '?')}%"
    if check == "silent_failure":
        return f"Collector blind for {t.get('runs', '?')} runs"
    return f"Tier-2: {check}"


def _facts_for(t):
    check = t.get("check")
    f = []
    if check == "capacity_incident":
        # Composite card: show every signal that fired for this incident, plus the primary
        # capacity metrics hoisted from whichever component surfaced them. This is what
        # replaces N separate throttle/pressure/extreme_peak cards for the same event.
        f = [("Signals firing", ", ".join(_SIGNAL_SHORT.get(s, s)
                                          for s in (t.get("signalTypes") or [])) or None),
             ("Peak CU", f"{t.get('peakCuPct')}%" if t.get("peakCuPct") is not None else None),
             ("Throttle", f"{t.get('throttleMinutes')} min"
              if t.get("throttleMinutes") is not None else None),
             ("Overage", f"{t.get('overageTotalMs')} ms"
              if t.get("overageTotalMs") is not None else None),
             ("Burndown", f"{t.get('minutesToBurndown')} min"
              if t.get("minutesToBurndown") is not None else None)]
        pcts = t.get("thresholdPcts") or {}
        if pcts:
            f.append(("Fabric thresholds",
                      ", ".join(f"{k}={v}%" for k, v in pcts.items())))
        # B3 correlated user spikes — the biggest actionable payoff of Design A': the human
        # sees "capacity throttled AND this specific user's query was 8x their p95 in the
        # same window" on ONE card. Top 3 shown to keep the card readable; the notification-
        # center chat has the full list.
        spikes = t.get("correlatedUserSpikes") or []
        if spikes:
            lines = []
            for sp in spikes[:3]:
                user = sp.get("user") or "unknown"
                cu = sp.get("cuSeconds")
                ratio = sp.get("ratio")
                item = sp.get("item")
                op = sp.get("operation")
                bit = user
                if op or item:
                    bit += f" ({op or 'operation'}"
                    if item:
                        bit += f" on {item}"
                    bit += ")"
                metric = []
                if cu is not None:
                    metric.append(f"{cu} CPU-s")
                if ratio is not None:
                    metric.append(f"{ratio}x baseline")
                if metric:
                    bit += " — " + ", ".join(metric)
                lines.append(bit)
            more = len(spikes) - len(lines)
            joined = "; ".join(lines)
            if more > 0:
                joined += f" (+{more} more in chat)"
            f.append(("Correlated user spikes", joined))
        return [(n, v) for n, v in f if v is not None and "None" not in str(v)]
    if check == "concentration":
        f = [("Item", t.get("item")), ("Workspace", t.get("workspace")),
             ("Share", f"{t.get('sharePct')}%"), ("Owner", t.get("owner"))]
        tu = t.get("topUsers")
        if tu:
            top = tu[0]
            f.append(("Top user", top.get("user") if isinstance(top, dict) else top))
    elif check == "throttle":
        f = [("Throttle", f"{t.get('throttleMinutes')} min"), ("Peak CU", f"{t.get('peakCuPct')}%")]
    elif check == "pressure":
        f = [("Peak CU", f"{t.get('peakCuPct')}%")]
    elif check == "extreme_peak":
        f = [("Peak CU", f"{t.get('peakCuPct')}%"),
             ("Extreme threshold", f"{t.get('extremeThreshold')}%")]
    elif check == "throttle_imminent":
        pcts = t.get("thresholdPcts") or {}
        f = [("Worst threshold pct", f"{t.get('worstPct')}%"),
             ("Peak CU", f"{t.get('peakCuPct')}%" if t.get("peakCuPct") is not None else None),
             ("Signals", ", ".join(f"{k}={v}%" for k, v in pcts.items()) if pcts else None)]
    elif check == "overage":
        f = [("Overage", f"{t.get('overageTotalMs')} ms"),
             ("Burndown", f"{t.get('minutesToBurndown')} min")]
    elif check == "cross_user":
        users = t.get("users") or []
        f = [("Item", t.get("item")), ("Workspace", t.get("workspace")),
             ("Distinct users", t.get("userCount")), ("Item share", f"{t.get('sharePct')}%"),
             ("Users", ", ".join(str(u) for u in users[:5]) if users else None)]
    elif check == "blind_spot":
        f = [("Peak CU", f"{t.get('peakCuPct')}%"), ("Monitored items", "0 (no attribution)")]
    elif check == "sustained":
        f = [("Peak CU", f"{t.get('peakCuPct')}%"), ("In band", f"{t.get('minutesInBand')} min"),
             ("Band", f"{t.get('bandLow')}-{t.get('bandHigh')}%")]
    elif check == "rate_change":
        f = [("From", f"{t.get('prevCuPct')}%"), ("To", f"{t.get('peakCuPct')}%"),
             ("Rise", f"+{t.get('risePts')} pts / 5 min")]
    elif check == "silent_failure":
        f = [("Blind runs", t.get("runs"))]
    if (t.get("recurrence") or {}).get("isRecurring"):
        f.append(("Recurrence", "recurring (matches prior findings)"))
    # Capacity + attribution are SEPARATE facts (Part 5): for attribution checks, when this window's
    # true CU% is available, surface it as its OWN fact — never computed FROM the attribution share
    # above. Omitted (not fabricated) when the trigger carries no capacity reading.
    if check in ("concentration", "cross_user"):
        cap_peak = t.get("capacityPeakCuPct")
        if cap_peak is not None:
            # BUG 4 fix: "(no throttle)" was unconditional — fabricated even at e.g. 96% peak
            # with a live throttle. Only claim "no throttle" when that's actually confirmed
            # (no throttle minutes AND peak below 100%); otherwise state the honest number,
            # optionally flagging an elevated peak, never a claim we can't back up.
            throttle_min = t.get("capacityThrottleMinutes")
            try:
                peak_val = float(cap_peak)
            except (TypeError, ValueError):
                peak_val = None
            confirmed_no_throttle = throttle_min in (None, 0) and peak_val is not None and peak_val < 100
            if confirmed_no_throttle:
                f.append(("Capacity this window", f"{cap_peak}% (no throttle)"))
            elif peak_val is not None and peak_val >= 100:
                f.append(("Capacity this window", f"{cap_peak}% (elevated)"))
            else:
                f.append(("Capacity this window", f"{cap_peak}%"))
    return [(n, v) for n, v in f if v is not None and "None" not in str(v)]


def _workspace_from_key(key):
    """Derive the workspace from an incident key: ``cross_user::Fin/Sales`` -> ``Fin``;
    ``throttle::capacity`` -> None. Used to keep the ticket's workspace stable on the inactive tick,
    when no trigger is present to read it from."""
    try:
        rest = key.split("::", 1)[1]
    except (AttributeError, IndexError):
        return None
    return rest.split("/", 1)[0] if "/" in rest else None


def _investigate_query(t, *, prefix=None, when=None):
    """The prompt auto-sent when the alert deep-link is opened — kicks off a live agent
    investigation (real MCP tools), so clicking the card gives the root cause, not just facts.

    ``prefix`` (optional) carries ticket memory the agent cannot otherwise see — e.g. on a recurrence
    of a human-resolved ticket, the prior resolution note — so the auto-investigation opens knowing
    it is a standing ticket, not a blank slate (Step 7 ticket memory).

    ``when`` (the fire time) anchors the investigation to when the alert fired. Without it the agent
    investigates the live 'now' — often hours after the click, when the event has passed — and wrongly
    concludes nothing is wrong or the named user wasn't the driver."""
    check = t.get("check")
    lead = (prefix.strip() + " ") if prefix else ""
    anchor = ""
    if when:
        anchor = (f" This alert fired around {when} — investigate the capacity and activity IN THAT "
                  "TIME WINDOW as your primary anchor (use it as a direction, ±30 min), not the current "
                  "moment; the live 'now' may look clean because the event has already passed. If that "
                  "±30-min window does NOT corroborate the named user/finding (they don't appear among "
                  "the top actors there, or their activity is trivial), do NOT just widen the same "
                  "window — PIVOT: search the named user's own activity broadly (last 7-30 days) to "
                  "find when THEY were actually most active/anomalous, and investigate THAT time "
                  "instead.")
    return (f"{lead}Investigate this {check} alert and give me the root cause. {_title_for(t)}.{anchor} "
            "Pull the recent capacity + activity, identify the top consumers and any expensive "
            "operations or refresh contention driving it, and tell me what's causing it and what "
            "to do. Distinguish true CU% (ground truth) from the monitored-activity proxy — do not "
            "present the proxy as capacity consumption.")


_COMPOSABLE_CAPACITY_CHECKS = ("throttle", "pressure", "overage", "extreme_peak",
                                "throttle_imminent")


def _coalesce_capacity_family(triggers):
    """Design A' (2026-08-09) — collapse the five capacity-family checks
    (throttle/pressure/overage/extreme_peak/throttle_imminent) into a single composite
    ``capacity_incident`` trigger per capacity, so multiple signal types firing for the
    SAME underlying event produce ONE Teams card instead of N redundant ones. All other
    trigger types (concentration / cross_user / blind_spot / sustained / rate_change /
    silent_failure / data_unavailable) pass through unchanged.

    The composite carries a ``signals`` list of the original triggers plus aggregated
    metrics (peakCuPct, throttleMinutes, overage fields, worst threshold pcts) hoisted
    from whichever component surfaced them. Downstream _title_for / _facts_for read the
    composite; severity_of / primary_metric / is_escalation know the check type.
    """
    fam = [t for t in triggers if t.get("check") in _COMPOSABLE_CAPACITY_CHECKS]
    others = [t for t in triggers if t.get("check") not in _COMPOSABLE_CAPACITY_CHECKS]
    if len(fam) < 2:
        # 0 or 1 family triggers → no coalescing needed. A single family trigger keeps its
        # original shape (no composite wrapping) so it dedupes with prior-run composites via
        # the shared incident_key. incident_key promotes throttle/pressure/etc. keys onto the
        # capacity:: namespace already, so this is safe.
        return triggers
    by_cap = {}
    for t in fam:
        cap = t.get("capacityId") or "capacity"
        by_cap.setdefault(cap, []).append(t)
    composites = []
    for cap_id, sigs in by_cap.items():
        if len(sigs) == 1:
            composites.append(sigs[0])
            continue
        composite = {"check": "capacity_incident", "capacityId": cap_id,
                     "signals": sigs, "signalTypes": [s.get("check") for s in sigs]}
        # Hoist the primary numeric facts from whichever component signal surfaced them.
        for k in ("peakCuPct", "throttleMinutes", "overageTotalMs", "overageCumulativePct",
                  "minutesToBurndown"):
            for s in sigs:
                if s.get(k) is not None:
                    composite[k] = s[k]
                    break
        # Threshold pcts (from throttle_imminent) — the composite carries them so a single
        # card can show that Fabric's own signals were at the edge, not just that CU crossed.
        for s in sigs:
            if s.get("thresholdPcts"):
                composite["thresholdPcts"] = s["thresholdPcts"]
                composite["worstPct"] = s.get("worstPct")
                break
        # B3 correlation booster — hoist per-user spikes that overlap this incident from any
        # component. All components at this point correlate to the SAME capacity event (they
        # share capacityId), so their spike lists are equivalent; take the first non-empty.
        # De-duped by user so the composite doesn't repeat the same person if multiple
        # components each attached them.
        seen_users = set()
        merged_spikes = []
        for s in sigs:
            for sp in s.get("correlatedUserSpikes") or []:
                u = sp.get("user")
                if u and u in seen_users:
                    continue
                if u:
                    seen_users.add(u)
                merged_spikes.append(sp)
        if merged_spikes:
            composite["correlatedUserSpikes"] = merged_spikes
        # A single normalityHint for the composite — chosen from the highest-priority signal
        # (throttle > extreme_peak > overage > pressure > throttle_imminent).
        priority = {"throttle": 0, "extreme_peak": 1, "overage": 2, "pressure": 3,
                    "throttle_imminent": 4}
        primary_sig = min(sigs, key=lambda s: priority.get(s.get("check"), 99))
        composite["normalityHint"] = primary_sig.get("normalityHint")
        composites.append(composite)
    return composites + others


def process_alerts(triggers, *, alerts_store, delivery_sinks, reasoner=None,
                   chat_writer=None, app_url="", cfg=None, now_dt=None, reminder_hours=48,
                   ack_store=None, ticket_writer=None, health=None):
    """Run the alert state machine over the current triggers. Returns an action summary.

    Ordering is cost-critical: the deterministic dedup + materiality checks decide silence WITHOUT
    calling the LLM; ``reasoner`` (the investigation) runs only for a new report/ambiguous incident
    or an escalation. Reminders reuse the stored investigation summary (no LLM). All sends route
    through ``outbound.dispatch_outbound`` (egress chokepoint).

    ``health``: optional ``automation.health.HealthReport`` — the chat-write / ticket-write / reopen
    failures below are already logged (WARN prints); this additionally records them so a degraded
    delivery path surfaces in the digest banner instead of only in job logs.
    """
    from ..outbound import dispatch_outbound
    from ..adapters.delivery_webhook import build_card, PROXY_RANKING_DISCLOSURE

    cfg = cfg if cfg is not None else load_cfg()
    # Design A': collapse multi-signal capacity firings into a single composite BEFORE dedup so
    # the same underlying incident produces one card, not N. Non-capacity triggers pass through.
    triggers = _coalesce_capacity_family(triggers)
    now_dt = now_dt if now_dt is not None else datetime.now(timezone.utc)
    now_iso = now_dt.isoformat().replace("+00:00", "Z")
    active = alerts_store["query_active"]()
    # Hysteresis (Step 2.3): attribution signals must persist N consecutive checks before alerting.
    _ATTR_CHECKS = ("concentration", "cross_user")
    # Only these hard capacity incidents are pushed to Teams. Attribution / coverage findings are
    # notification-center-only (see _send) — Teams stays reserved for genuine capacity emergencies.
    _TEAMS_CHECKS = ("throttle", "pressure", "overage", "extreme_peak", "throttle_imminent",
                     "capacity_incident")
    hysteresis_ticks = int(cfg.get("hysteresis_ticks", 3))
    pending = alerts_store["query_pending"]() if "query_pending" in alerts_store else {}
    pending_seen = set()
    seen = set()
    # FIX A: is there a REAL capacity event firing this run? (throttle/pressure/overage = true-CU
    # over-threshold). A recurring attribution pattern only earns a live ticket when it actually
    # correlates with one of these; absent that, it's a known-stable pattern, logged informational.
    capacity_linked = any((t.get("check") in _TEAMS_CHECKS) for t in triggers)
    actions = {"new": [], "escalation": [], "reminder": [], "resolved": [], "silent": [],
               "inactive": [], "reopened": [], "pending": [], "informational": []}

    def _send(kind, trigger, row, summary, *, investigate_prefix=None):
        # Teams is reserved for CAPACITY EMERGENCIES only. Attribution issues (concentration /
        # cross-user / coverage gaps) are surfaced in the app's notification center — pushing every
        # one to the Teams channel is the repetitive noise the user asked us to stop. The ticket is
        # still fully created (chat + audit_alerts row + alert_ticket) so it shows in the center;
        # we just don't post a card here.
        if trigger.get("check") not in _TEAMS_CHECKS:
            return False
        cid = row.get("chatId")
        chat_url = None
        if app_url and kind != "resolved":
            # Real chat id -> open THAT conversation; no id (write failed) -> the app root, which
            # opens a fresh chat. Either way ?query auto-runs a live investigation on open, so the
            # link is always present AND always resolves (never a fake /chat/<uuid> that 404s).
            base = f"{app_url.rstrip('/')}/chat/{cid}" if cid else f"{app_url.rstrip('/')}/"
            chat_url = base + "?query=" + urllib.parse.quote(
                _investigate_query(trigger, prefix=investigate_prefix, when=now_iso))
        # Concentration/attribution alerts rank a CPU-time PROXY, not true CU — the card must say so.
        disclosure = (PROXY_RANKING_DISCLOSURE
                      if trigger.get("check") in ("concentration", "cross_user") else None)
        # "When / first noticed" (Part 5): sourced from the incident row's firstAlertedAt, falling
        # back to runAt when the row has no first-alerted timestamp yet. Uses the repo's canonical
        # display-time helper (never hand-rolled tz math); falls back to the raw ISO string if the
        # timestamp doesn't parse.
        facts = list(_facts_for(trigger))
        when_raw = row.get("firstAlertedAt") or row.get("runAt")
        if when_raw:
            facts.append(("When", to_display(when_raw) or when_raw))
        card = build_card(kind, title=_title_for(trigger), severity=row.get("severity", "info"),
                          facts=facts, summary=summary, chat_url=chat_url,
                          disclosure=disclosure)
        res = dispatch_outbound("tier2_alert", {"attachments": [card]}, sinks=delivery_sinks)
        return bool(res.get("delivered"))

    def _write_ticket(row, trigger):
        """Step 9: mirror this ticket's descriptive metadata into the app-readable store (Lakebase
        ``alert_ticket``) keyed by the stable incidentKey (chat_id is carried along but nullable),
        so the Alerts sidebar can show what/where/when/active. ALWAYS written — even when chat_id is
        None (chat creation failed) — so a real incident is never silently dropped from the
        notification center just because the chat write failed (Part 7). Lifecycle
        (open/investigating/resolved) is derived app-side from the ack map — not stored here.
        Failure-isolated: a metadata write must never drop or fail a real alert."""
        cid = row.get("chatId")
        if not ticket_writer:
            return
        # workspace comes from the trigger when firing; on the inactive tick there is no trigger, so
        # fall back to the incident key ("cross_user::Fin/Sales" -> "Fin") to avoid nulling it.
        ws = trigger.get("workspace") or _workspace_from_key(row.get("incidentKey"))
        try:
            ticket_writer(cid, {
                "incidentKey": row.get("incidentKey"),
                "checkType": row.get("checkType"),
                "severity": row.get("severity"),
                "resource": row.get("resource"),
                "workspace": ws,
                "detail": row.get("materialityReason") or row.get("investigationSummary") or "",
                "firstDetected": row.get("firstAlertedAt"),
                "currentlyActive": row.get("currentlyActive"),
            })
            if health is not None:
                health.record_delivery("ticket", True)
        except Exception as exc:
            print(f"[tier2] ticket metadata write failed ({type(exc).__name__}: {exc})")
            if health is not None:
                health.record_delivery("ticket", False, f"{type(exc).__name__}: {exc}")

    for t in triggers:
        if t.get("check") == "data_unavailable":
            continue
        key = incident_key(t)
        seen.add(key)
        prior = active.get(key)
        sev = severity_of(t)
        metric = primary_metric(t)

        if prior:  # already-active incident
            _ticket = (ack_store["get"](prior.get("chatId"))
                       if (ack_store and prior.get("chatId")) else None)
            _resolved = bool(_ticket) and (_ticket.get("status") or "").lower() == "resolved"
            if _resolved and prior.get("currentlyActive") is False:
                # RECURRENCE after a human Resolve (Step 8): reopen the SAME ticket + re-alert,
                # rather than staying silent or minting a new one.
                try:
                    if ack_store.get("reopen"):
                        ack_store["reopen"](prior.get("chatId"))
                except Exception as exc:
                    print(f"[tier2] reopen failed ({type(exc).__name__}: {exc})")
                    if health is not None:
                        health.record_issue(f"ticket reopen failed: {type(exc).__name__}: {exc}")
                _note = _ticket.get("resolutionNote")
                summary = ("Recurred after being marked resolved"
                           + (f" — prior note: {_note}" if _note else "") + ".")
                # Ticket memory (Step 7): tell the auto-investigation this is a recurrence of a
                # human-resolved ticket + the prior note, so it opens knowing the history.
                _prefix = ("NOTE: this ticket was previously marked RESOLVED"
                           + (f" (prior resolution note: \"{_note}\")" if _note else "")
                           + " and has now RECURRED. First decide whether the same cause returned or "
                           "a new driver is behind it this time.")
                row = dict(prior, currentlyActive=True, status="active", lastAlertedAt=now_iso,
                           runAt=now_iso, investigationSummary=summary, absenceCount=0)
                row["delivered"] = _send("new", t, row, summary, investigate_prefix=_prefix)
                alerts_store["upsert"](row)
                _write_ticket(row, t)
                actions["reopened"].append(key)
            elif is_escalation(t, {"severity": prior.get("severity"), "metric": prior.get("metric"),
                                    "signalTypes": prior.get("signalTypes"),
                                    "throttleMinutes": prior.get("throttleMinutes")}, cfg):
                inv = reasoner(t) if reasoner else {"markdown": "", "summary": "", "report": True}
                row = dict(prior, severity=sev, metric=metric, lastAlertedAt=now_iso, runAt=now_iso,
                           currentlyActive=True, absenceCount=0,
                           escalationCount=(prior.get("escalationCount") or 0) + 1,
                           investigationSummary=inv.get("summary") or prior.get("investigationSummary"))
                # Composite state: carry signal set + primary metrics so the next tick can
                # decide "new signal joined" or "throttle worsened" without re-fetching.
                if t.get("check") == "capacity_incident":
                    row["signalTypes"] = t.get("signalTypes") or []
                    if t.get("throttleMinutes") is not None:
                        row["throttleMinutes"] = t["throttleMinutes"]
                row["delivered"] = _send("new", t, row, inv.get("summary"))
                alerts_store["upsert"](row)
                _write_ticket(row, t)
                actions["escalation"].append(key)
            else:
                # An already-open incident that is STILL firing does NOT re-post to Teams. The
                # persistent "still open" surface is the app's notification center now — re-sending a
                # card every reminder window is exactly the repetition the user asked us to stop
                # ("recurring tickets don't come up in Teams / aren't repeated"). We only keep the
                # ticket fresh (currentlyActive=True) so the center reflects reality, and stay silent.
                # A genuine WORSENING still breaks through (escalation, checked above), and a
                # recurrence AFTER a human resolve still re-alerts (reopen branch, checked above).
                if prior.get("currentlyActive") is not True:
                    # Re-fire during the quiet-grace window: same incident, no new card. Reset
                    # absenceCount so the next absent tick starts the grace clock from scratch.
                    row = dict(prior, runAt=now_iso, currentlyActive=True, absenceCount=0)
                    alerts_store["upsert"](row)
                    _write_ticket(row, t)
                actions["silent"].append(key)
            continue

        # new incident: deterministic decision, LLM only for report/ambiguous
        decision, reason = classify(t, cfg)
        if decision == "suppress":
            actions["silent"].append(key)
            if key in pending:  # a fluctuating candidate dropped below the bar -> reset its streak
                alerts_store.get("delete", lambda _k: None)(key)
            continue
        # Hysteresis: an attribution candidate must be present for N consecutive checks before it
        # alerts (physical capacity checks alert immediately). Track a pending streak; promote when
        # it reaches the threshold. Prevents a single-tick share wobble from paging anyone.
        if t.get("check") in _ATTR_CHECKS and hysteresis_ticks > 1:
            p = pending.get(key)
            count = ((p.get("presenceCount") or 0) if p else 0) + 1
            pending_seen.add(key)
            if count < hysteresis_ticks:
                alerts_store["upsert"]({
                    "incidentKey": key, "status": "pending", "severity": sev,
                    "checkType": t.get("check"),
                    "resource": t.get("item") or t.get("workspace") or "capacity",
                    "presenceCount": count, "metric": metric,
                    "firstAlertedAt": (p.get("firstAlertedAt") if p else now_iso),
                    "runAt": now_iso, "delivered": False})
                actions["pending"].append(key)
                continue
            # sustained across the window -> promote to a real incident (falls through to alert)
        # FIX A: a RECURRING attribution pattern (concentration / cross_user) earns a live ticket +
        # reasoner call ONLY if it correlates with a real capacity event this run. A stable,
        # already-known pattern with no true-CU linkage is logged informational-only (feeds the daily
        # digest) — the deterministic layer must not mint a "go investigate this person" ticket that
        # the LLM investigation would just conclude isn't the driver. A genuinely NEW pattern (not
        # recurring) always reports; a recurring one that IS capacity-linked reports (real event).
        if (t.get("check") in _ATTR_CHECKS
                and (t.get("recurrence") or {}).get("isRecurring")
                and not capacity_linked):
            alerts_store["upsert"]({
                "incidentKey": key, "status": "informational", "severity": sev,
                "checkType": t.get("check"),
                "resource": t.get("item") or t.get("workspace") or "capacity",
                "metric": metric, "materialityReason": reason,
                "firstAlertedAt": (pending.get(key, {}).get("firstAlertedAt") or now_iso),
                "runAt": now_iso, "delivered": False, "currentlyActive": True})
            actions["informational"].append(key)
            continue
        inv = reasoner(t) if reasoner else {"markdown": "", "summary": "", "report": decision == "report"}
        report = True if decision == "report" else bool(inv.get("report"))
        if not report:
            actions["silent"].append(key)
            continue
        markdown = inv.get("markdown") or _title_for(t)
        summary = inv.get("summary") or ""
        chat_id = None
        if chat_writer:
            try:
                chat_id = chat_writer(markdown, _title_for(t))
                if health is not None:
                    health.record_delivery("chat", True)
            except Exception as exc:  # a chat-write failure must not drop the alert or the link
                print(f"[tier2] WARN: alert chat write failed ({type(exc).__name__}: {exc}); "
                      "deep-link will open a fresh auto-investigating chat; the ticket is still "
                      "written (keyed by incidentKey, not chat_id)")
                if health is not None:
                    health.record_delivery("chat", False, f"{type(exc).__name__}: {exc}")
        # chat_id may be None (writer absent or failed) -> _send falls back to a root ?query link
        # that opens a fresh auto-investigating chat. No fake /chat/<uuid> (that 404s).
        row = {"incidentKey": key, "status": "active", "severity": sev, "checkType": t.get("check"),
               "resource": t.get("item") or t.get("workspace") or "capacity", "chatId": chat_id,
               "metric": metric, "firstAlertedAt": now_iso, "lastAlertedAt": now_iso,
               "lastRemindedAt": None, "resolvedAt": None, "escalationCount": 0,
               "materialityReason": reason, "investigationSummary": summary,
               "delivered": False, "runAt": now_iso, "currentlyActive": True}
        # Composite state: carry signal set + primary metrics so the next tick's is_escalation
        # can decide "new signal joined" without a fresh trigger. Only for capacity_incident;
        # single-signal triggers keep the pre-Design-A' row shape.
        if t.get("check") == "capacity_incident":
            row["signalTypes"] = t.get("signalTypes") or []
            if t.get("throttleMinutes") is not None:
                row["throttleMinutes"] = t["throttleMinutes"]
        row["delivered"] = _send("new", t, row, summary)
        alerts_store["upsert"](row)
        _write_ticket(row, t)
        actions["new"].append(key)

    # resolution: incidents that were active but no longer fire this run
    _CAPACITY_CHECKS = ("throttle", "pressure", "overage", "extreme_peak", "throttle_imminent",
                        "capacity_incident")
    quiet_ticks = int(cfg.get("quiet_ticks", 12))
    for key, prior in active.items():
        if key in seen:
            continue
        if prior.get("checkType") in _CAPACITY_CHECKS:
            # Design A' quiet-to-resolve (2026-08-09): a capacity incident is held open through
            # up to ``quiet_ticks`` consecutive absent sweeps (default 12 = 60 min clean) before
            # actually resolving. A re-fire during that grace window is treated as the SAME
            # incident (no fresh card). This kills the resolve-then-re-fire flap that shipped a
            # duplicate card each time an event bounced across two adjacent sweep windows.
            absence = int(prior.get("absenceCount") or 0) + 1
            if absence < quiet_ticks:
                _row = dict(prior, absenceCount=absence, currentlyActive=False, runAt=now_iso)
                alerts_store["upsert"](_row)
                _write_ticket(_row, {"workspace": prior.get("workspace")})
                actions["inactive"].append(key)
            else:
                # Genuine physical capacity state that comes and goes -> auto-resolve internally,
                # but do NOT push a "resolved" card to Teams. Resolution isn't actionable — the
                # state cleared on its own, nothing for a human to do. Broadcasting the auto-close
                # doubled Teams volume without adding signal (found 2026-08-09). Notification-
                # center + audit_alerts still record the resolve state so the ticket lifecycle
                # stays intact.
                alerts_store["resolve"](key, now_iso)
                actions["resolved"].append(key)
        else:
            # Attribution / user-item finding (Step 8): its absence does NOT mean resolved and must
            # NOT send a "Resolved" card — that card + the next tick's re-fire IS the flapping. The
            # ticket stays open; we only flip a display-only currentlyActive flag. A human Resolve
            # (Step 9) is what closes it.
            if prior.get("currentlyActive") is not False:
                _row = dict(prior, currentlyActive=False, runAt=now_iso)
                alerts_store["upsert"](_row)
                _write_ticket(_row, {"workspace": prior.get("workspace")})
            actions["inactive"].append(key)

    # Hysteresis cleanup: a pending candidate that did NOT fire this run breaks its streak, so its
    # row is dropped — the count only survives while the condition holds every consecutive check.
    _delete = alerts_store.get("delete") if hasattr(alerts_store, "get") else None
    if _delete is not None:
        for key in pending:
            if key not in pending_seen and key not in seen:
                _delete(key)

    return actions


def _record_reading(readings_store, *, run_at, facts=None, collector_ok, health=None):
    """Append this run to the rolling-readings store and return the recent window (newest-first).
    Never fatal — a store error degrades to an empty history (stateful gates just won't fire)."""
    if readings_store is None:
        return []
    cap = (facts or {}).get("capacity") or {}
    items = (facts or {}).get("items") or []
    reading = {"runAt": run_at, "peakCuPct": cap.get("peakCuPct"),
               "throttleMinutes": cap.get("throttleMinutes"), "itemCount": len(items),
               "collectorOk": bool(collector_ok)}
    try:
        readings_store["append"](reading)
        return readings_store["recent"](12)
    except Exception as exc:
        print(f"[tier2] readings store unavailable ({type(exc).__name__}: {exc})")
        if health is not None:
            health.record_issue(f"readings store unavailable: {type(exc).__name__}: {exc}")
        return []


def run_tier2_check(collector, *, delivery_sinks=None, findings_store=None,
                    heartbeat_store=None, readings_store=None, config=None, tenant=None, scope=None,
                    alerts_store=None, reasoner=None, chat_writer=None, app_url="", now_dt=None,
                    ack_store=None, ticket_writer=None, health=None, baseline_store=None,
                    reporting_store=None):
    """Run one Tier 2 deterministic check. Zero LLM calls.

    ``collector``: a collector port ``{"collect": fn}`` — at minimum the Capacity Events collector.
    ``delivery_sinks``: reserved for Phase 10 (Entra bot identity); pass None for now.
    ``findings_store``: a ``{"query": fn}`` store for recurrence cross-reference (Phase 6).
    ``heartbeat_store``: a ``{"write": fn(timestamp)}`` store for self-observability (Task 9.4).
    ``readings_store``: a ``{"append","recent"}`` rolling store (Step 2) powering the STATEFUL gates
    (sustained-band, rate-of-change, silent-failure). None -> those gates simply don't fire.
    ``config``: detection config (uses DEFAULT_CONFIG if None).
    ``health``: optional ``automation.health.HealthReport`` — records the collector outcome
    (including any per-source failures merged collectors surface as ``facts["sourcesFailed"]``,
    adapters/collector_merge.py) plus everything ``process_alerts``/``_record_reading`` record.
    ``baseline_store``: optional B1/B2 user_baseline store — when provided, the sweep runs the
    precomputed baseline detector on ``facts["events"]`` and correlates any spikes against
    capacity triggers (B3 correlation booster). Spikes overlapping an active capacity incident
    are attached to the composite trigger so the ONE Teams card names the likely driver.
    ``reporting_store``: optional B4 capacity_reporting store — when provided, appends a
    per-sweep archival row (capacity metrics + which tier2 checks fired) for long-tail trend
    reports and post-incident review. Best-effort: a store failure never blocks the sweep.

    Returns ``{"triggered": bool, "triggers": list, "delivered": dict, "checkedAt": str}``.
    """
    from ..config import DEFAULT_CONFIG
    from .materiality import load_cfg
    config = config if config is not None else DEFAULT_CONFIG
    mcfg = load_cfg()
    checked_at = _now_iso()

    if heartbeat_store is not None:
        try:
            heartbeat_store["write"](checked_at)
        except Exception:
            pass

    def _deliver(trigs):
        if not (delivery_sinks and alerts_store is not None):
            return {}
        try:
            return process_alerts(trigs, alerts_store=alerts_store, delivery_sinks=delivery_sinks,
                                  reasoner=reasoner, chat_writer=chat_writer, app_url=app_url,
                                  now_dt=now_dt, ack_store=ack_store, ticket_writer=ticket_writer,
                                  health=health)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    try:
        facts = collector["collect"]()
    except Exception as exc:
        print(f"[tier2] collector FAILED: {type(exc).__name__}: {exc}")
        if health is not None:
            health.record_collector(None, False, f"{type(exc).__name__}: {exc}")
        # Record the failure so the silent-failure gate can fire after N consecutive blind runs.
        recent = _record_reading(readings_store, run_at=checked_at, facts=None, collector_ok=False,
                                 health=health)
        sf = _check_silent_failure(recent, mcfg)
        return {"triggered": bool(sf), "triggers": sf, "delivered": _deliver(sf),
                "checkedAt": checked_at, "error": "collector failed"}

    # Observability: what did the collector actually pull? (peakCuPct=None => no capacity data /
    # blind collector; a number => live data, and this is the live peak.)
    _cap = (facts or {}).get("capacity") or {}
    _items = (facts or {}).get("items") or []
    print(f"[tier2] pulled: peakCuPct={_cap.get('peakCuPct')} "
          f"throttleMinutes={_cap.get('throttleMinutes')} overageTotalMs={_cap.get('overageTotalMs')} "
          f"items={len(_items)}")

    if health is not None:
        # Reuse collector_merge's own per-source failure list (adapters/collector_merge.py) rather
        # than reclassifying — a merged collector already tells us WHICH sources failed and why.
        if (facts or {}).get("sourcesFailed"):
            health.record_collector_failures(facts["sourcesFailed"])
        else:
            health.record_collector("primary", True)

    _ok = _cap.get("peakCuPct") is not None or len(_items) > 0
    recent = _record_reading(readings_store, run_at=checked_at, facts=facts, collector_ok=_ok,
                             health=health)

    triggers = []
    triggers.extend(_check_concentration(facts, config))
    triggers.extend(_check_throttle(facts))
    triggers.extend(_check_pressure(facts))
    triggers.extend(_check_extreme_peak(facts, mcfg))
    triggers.extend(_check_throttle_imminent(facts, mcfg))
    triggers.extend(_check_overage(facts))
    triggers.extend(_check_same_item_cross_user(facts))
    triggers.extend(_check_cross_source_blind_spot(facts))
    triggers.extend(_check_sustained_band(recent, mcfg))
    triggers.extend(_check_rate_of_change(recent, mcfg))
    triggers.extend(_check_silent_failure(recent, mcfg))
    triggers.extend(_check_data_availability(facts))

    triggers = _cross_reference_recurrence(triggers, findings_store,
                                           scope=scope, tenant=tenant)

    # B3 correlation booster: if a baseline store is threaded in, run the precomputed baseline
    # detector on the current window's events and annotate any capacity triggers with user
    # spikes that fell in the same window. The composite capacity_incident card then names
    # the likely driver ("Bipin ran ExecuteQuery — 3389 CPU-s, 8x baseline") right on the
    # single Teams card, instead of surfacing it in a separate notification-center-only ticket.
    if baseline_store is not None:
        try:
            from ..detectors.user_baseline import detect_user_baseline_deviation_precomputed
            from .correlation import correlate_user_spikes_with_capacity
            spikes = detect_user_baseline_deviation_precomputed(facts, config,
                                                                 baseline_store=baseline_store)
            window_min = float(mcfg.get("correlation_window_min", 5.0))
            triggers = correlate_user_spikes_with_capacity(
                spikes, triggers, window_min=window_min, run_at=checked_at)
        except Exception as exc:
            print(f"[tier2] correlation booster skipped ({type(exc).__name__}: {exc})")
            if health is not None:
                health.record_issue(f"correlation booster failed: {type(exc).__name__}: {exc}")

    triggered = any(t.get("check") != "data_unavailable" for t in triggers)

    # B4 capacity_reporting: append one row per sweep with the full capacity snapshot + the
    # list of tier2 checks that fired. Runs BEFORE delivery so a delivery failure doesn't
    # cost us the historical row (the reporting archive is independent of alert lifecycle).
    # Best-effort: any store error degrades to a warning + health issue, never fails the sweep.
    if reporting_store is not None:
        try:
            from ..context_capacity_reporting import _extract_from_facts
            signal_types = sorted({t.get("check") for t in triggers
                                    if t.get("check") and t.get("check") != "data_unavailable"})
            reporting_store["append"](_extract_from_facts(
                facts, run_at=checked_at, signal_types=signal_types, collector_ok=_ok))
        except Exception as exc:
            print(f"[tier2] capacity_reporting append failed ({type(exc).__name__}: {exc})")
            if health is not None:
                health.record_issue(f"capacity_reporting append failed: "
                                    f"{type(exc).__name__}: {exc}")

    # Delivery: sub-project #2 wires the Tier-2 -> Teams alert path when the job provides a sink +
    # an alerts store (gated on TIER2_WEBHOOK_ENABLED upstream). Otherwise stays silent (no-op).
    delivered = _deliver(triggers)

    return {"triggered": triggered, "triggers": triggers,
            "delivered": delivered, "checkedAt": checked_at}
