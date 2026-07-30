"""Tier 2 cheap deterministic check — runs every 15 minutes, NO LLM calls (Phase 9).

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
from datetime import datetime, timezone

from ..investigation.gates import (
    concentration_gate,
    throttle_claim_gate,
    pressure_claim_gate,
    null_data_gate,
)


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
            triggers.append({
                "check": "concentration",
                "gate": result,
                "item": item.get("name"),
                "workspace": item.get("workspace"),
                "sharePct": share,
                "owner": item.get("owner"),
                "topUsers": item.get("topUsers"),
            })
    return triggers


def _check_throttle(facts):
    """Check throttle gate against capacity data.

    Returns a list of trigger dicts (at most one — throttle is capacity-wide).
    """
    cap = (facts or {}).get("capacity") or {}
    result = throttle_claim_gate(cap)
    if result["passed"]:
        return [{"check": "throttle", "gate": result,
                 "throttleMinutes": cap.get("throttleMinutes"),
                 "peakCuPct": cap.get("peakCuPct")}]
    return []


def _check_pressure(facts):
    """Check CU-pressure gate (peakCuPct > 100, even without a throttle signal)."""
    cap = (facts or {}).get("capacity") or {}
    result = pressure_claim_gate(cap)
    if result["passed"]:
        return [{"check": "pressure", "gate": result,
                 "peakCuPct": cap.get("peakCuPct")}]
    return []


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
                     "minutesToBurndown": cap.get("minutesToBurndown")}]
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
                "throttle": "capacity.throttle",
                "pressure": "capacity.pressure",
                "overage": "capacity.overage",
            }
            prefix = key_prefixes.get(check, "")
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


def run_tier2_check(collector, *, delivery_sinks=None, findings_store=None,
                    heartbeat_store=None, config=None, tenant=None, scope=None):
    """Run one Tier 2 deterministic check. Zero LLM calls.

    ``collector``: a collector port ``{"collect": fn}`` — at minimum the Capacity Events collector.
    ``delivery_sinks``: ``{"teams": teams_sink, "email": email_sink}`` — both optional/inert.
    ``findings_store``: a ``{"query": fn}`` store for recurrence cross-reference (Phase 6).
    ``heartbeat_store``: a ``{"write": fn(timestamp)}`` store for self-observability (Task 9.4).
    ``config``: detection config (uses DEFAULT_CONFIG if None).

    Returns ``{"triggered": bool, "triggers": list, "delivered": dict, "checkedAt": str}``.
    Failure-isolated — a delivery error never prevents the check from completing.
    """
    from ..config import DEFAULT_CONFIG
    config = config if config is not None else DEFAULT_CONFIG
    checked_at = _now_iso()

    # Write heartbeat FIRST so even a failed check updates the timestamp (Task 9.4).
    if heartbeat_store is not None:
        try:
            heartbeat_store["write"](checked_at)
        except Exception:
            pass

    # Collect data — failure here is the check failing, not an alert-path error.
    try:
        facts = collector["collect"]()
    except Exception:
        return {"triggered": False, "triggers": [], "delivered": {},
                "checkedAt": checked_at, "error": "collector failed"}

    # Run deterministic checks in priority order.
    triggers = []
    triggers.extend(_check_concentration(facts, config))   # 1. concentration (PRIMARY)
    triggers.extend(_check_throttle(facts))                 # 2. throttle (PRIMARY)
    triggers.extend(_check_pressure(facts))                 # 3. CU pressure > 100%
    triggers.extend(_check_overage(facts))                  # 4. overage/burndown
    triggers.extend(_check_data_availability(facts))        # 5. data availability (STOP gate)

    # Cross-reference recent findings for recurrence detection.
    triggers = _cross_reference_recurrence(triggers, findings_store,
                                           scope=scope, tenant=tenant)

    triggered = any(t.get("check") != "data_unavailable" for t in triggers)

    delivered = {}
    if triggered:
        summary = _build_tier2_alert_summary(triggers)
        alert_payload = {
            "summary": summary,
            "tier": "tier2",
            "checkedAt": checked_at,
            "triggers": triggers,
        }
        # Deliver through the outbound allowlist — Teams (primary), email (secondary).
        # Each channel is independently failure-isolated.
        try:
            from ..outbound import dispatch_outbound
            if (delivery_sinks or {}).get("teams"):
                try:
                    res = dispatch_outbound("teams_notify", alert_payload,
                                            sinks={"teams": delivery_sinks["teams"]})
                    delivered["teams"] = res.get("delivered", False)
                except Exception:
                    delivered["teams"] = False
            if (delivery_sinks or {}).get("email"):
                try:
                    res = dispatch_outbound("email_notify", alert_payload,
                                            sinks={"email": delivery_sinks["email"]})
                    delivered["email"] = res.get("delivered", False)
                except Exception:
                    delivered["email"] = False
        except Exception:
            pass

    return {"triggered": triggered, "triggers": triggers,
            "delivered": delivered, "checkedAt": checked_at}
