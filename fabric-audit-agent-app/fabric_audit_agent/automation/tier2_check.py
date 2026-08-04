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
import urllib.parse
import uuid
from datetime import datetime, timezone

from ..investigation.gates import (
    concentration_gate,
    throttle_claim_gate,
    pressure_claim_gate,
    null_data_gate,
)
from .incident import incident_key, severity_of, primary_metric
from .materiality import classify, is_escalation, load_cfg


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
            hint = ("High share — likely automated/scheduled or runaway process; "
                    "verify if this is a known batch job" if share >= 50
                    else "Moderate share — may be a large legitimate user run; "
                         "check if this matches a known scheduled job or report")
            triggers.append({
                "check": "concentration",
                "gate": result,
                "item": item.get("name"),
                "workspace": item.get("workspace"),
                "sharePct": share,
                "owner": item.get("owner"),
                "topUsers": item.get("topUsers"),
                "normalityHint": hint,
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
                 "peakCuPct": cap.get("peakCuPct"),
                 "normalityHint": "Capacity exceeded its throttle threshold — check if this "
                                  "coincides with a scheduled refresh or batch window"}]
    return []


def _check_pressure(facts):
    """Check CU-pressure gate (peakCuPct > 100, even without a throttle signal)."""
    cap = (facts or {}).get("capacity") or {}
    result = pressure_claim_gate(cap)
    if result["passed"]:
        return [{"check": "pressure", "gate": result,
                 "peakCuPct": cap.get("peakCuPct"),
                 "normalityHint": "CU exceeded 100% but throttle not yet confirmed — watch "
                                  "for escalation in the next few checks"}]
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
                     "minutesToBurndown": cap.get("minutesToBurndown"),
                     "normalityHint": "Overage is accumulating — if this is a one-off large "
                                      "job it will burn down; if it persists across multiple "
                                      "checks it's a pattern"}]
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


# ---- Alerting orchestration (sub-project #2): dedup/materiality FIRST, LLM only when alerting ----

def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _title_for(t):
    check = t.get("check")
    if check == "concentration":
        return f"Concentration: {t.get('item', '?')} at {t.get('sharePct', '?')}%"
    if check == "throttle":
        return f"Throttling on capacity ({t.get('throttleMinutes', '?')} min)"
    if check == "pressure":
        return f"CU pressure: peak {t.get('peakCuPct', '?')}%"
    if check == "overage":
        return "Capacity overage accumulating"
    return f"Tier-2: {check}"


def _facts_for(t):
    check = t.get("check")
    f = []
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
    elif check == "overage":
        f = [("Overage", f"{t.get('overageTotalMs')} ms"),
             ("Burndown", f"{t.get('minutesToBurndown')} min")]
    if (t.get("recurrence") or {}).get("isRecurring"):
        f.append(("Recurrence", "recurring (matches prior findings)"))
    return [(n, v) for n, v in f if v is not None and "None" not in str(v)]


def _investigate_query(t):
    """The prompt auto-sent when the alert deep-link is opened — kicks off a live agent
    investigation (real MCP tools), so clicking the card gives the root cause, not just facts."""
    check = t.get("check")
    return (f"Investigate this {check} alert and give me the root cause. {_title_for(t)}. "
            "Pull the recent capacity + activity, identify the top consumers and any expensive "
            "operations or refresh contention driving it, and tell me what's causing it and what "
            "to do. Distinguish true CU% (ground truth) from the monitored-activity proxy — do not "
            "present the proxy as capacity consumption.")


def process_alerts(triggers, *, alerts_store, delivery_sinks, reasoner=None,
                   chat_writer=None, app_url="", cfg=None, now_dt=None, reminder_hours=48):
    """Run the alert state machine over the current triggers. Returns an action summary.

    Ordering is cost-critical: the deterministic dedup + materiality checks decide silence WITHOUT
    calling the LLM; ``reasoner`` (the investigation) runs only for a new report/ambiguous incident
    or an escalation. Reminders reuse the stored investigation summary (no LLM). All sends route
    through ``outbound.dispatch_outbound`` (egress chokepoint).
    """
    from ..outbound import dispatch_outbound
    from ..adapters.delivery_webhook import build_card

    cfg = cfg if cfg is not None else load_cfg()
    now_dt = now_dt if now_dt is not None else datetime.now(timezone.utc)
    now_iso = now_dt.isoformat().replace("+00:00", "Z")
    active = alerts_store["query_active"]()
    seen = set()
    actions = {"new": [], "escalation": [], "reminder": [], "resolved": [], "silent": []}

    def _send(kind, trigger, row, summary):
        cid = row.get("chatId")
        chat_url = None
        if app_url and cid:
            chat_url = f"{app_url.rstrip('/')}/chat/{cid}"
            if kind != "resolved":  # opening the link auto-runs a live investigation
                chat_url += "?query=" + urllib.parse.quote(_investigate_query(trigger))
        card = build_card(kind, title=_title_for(trigger), severity=row.get("severity", "info"),
                          facts=_facts_for(trigger), summary=summary, chat_url=chat_url)
        res = dispatch_outbound("tier2_alert", {"attachments": [card]}, sinks=delivery_sinks)
        return bool(res.get("delivered"))

    for t in triggers:
        if t.get("check") == "data_unavailable":
            continue
        key = incident_key(t)
        seen.add(key)
        prior = active.get(key)
        sev = severity_of(t)
        metric = primary_metric(t)

        if prior:  # already-active incident
            if is_escalation(t, {"severity": prior.get("severity"), "metric": prior.get("metric")}, cfg):
                inv = reasoner(t) if reasoner else {"markdown": "", "summary": "", "report": True}
                row = dict(prior, severity=sev, metric=metric, lastAlertedAt=now_iso, runAt=now_iso,
                           escalationCount=(prior.get("escalationCount") or 0) + 1,
                           investigationSummary=inv.get("summary") or prior.get("investigationSummary"))
                row["delivered"] = _send("new", t, row, inv.get("summary"))
                alerts_store["upsert"](row)
                actions["escalation"].append(key)
            else:
                last = _parse_iso(prior.get("lastRemindedAt")) or _parse_iso(prior.get("lastAlertedAt"))
                due = last is None or (now_dt - last).total_seconds() >= reminder_hours * 3600
                if due:
                    row = dict(prior, lastRemindedAt=now_iso, runAt=now_iso, severity=sev, metric=metric)
                    row["delivered"] = _send("reminder", t, row, prior.get("investigationSummary"))
                    alerts_store["upsert"](row)
                    actions["reminder"].append(key)
                else:
                    actions["silent"].append(key)
            continue

        # new incident: deterministic decision, LLM only for report/ambiguous
        decision, reason = classify(t, cfg)
        if decision == "suppress":
            actions["silent"].append(key)
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
            except Exception as exc:  # a chat-write failure must not drop the alert or the link
                print(f"[tier2] alert chat write failed ({type(exc).__name__}: {exc}); "
                      "deep-link will open a fresh auto-investigating chat")
        if not chat_id:
            chat_id = str(uuid.uuid4())  # link ALWAYS present; ?query auto-investigates on open
        row = {"incidentKey": key, "status": "active", "severity": sev, "checkType": t.get("check"),
               "resource": t.get("item") or t.get("workspace") or "capacity", "chatId": chat_id,
               "metric": metric, "firstAlertedAt": now_iso, "lastAlertedAt": now_iso,
               "lastRemindedAt": None, "resolvedAt": None, "escalationCount": 0,
               "materialityReason": reason, "investigationSummary": summary,
               "delivered": False, "runAt": now_iso}
        row["delivered"] = _send("new", t, row, summary)
        alerts_store["upsert"](row)
        actions["new"].append(key)

    # resolution: incidents that were active but no longer fire this run
    for key, prior in active.items():
        if key in seen:
            continue
        title = f"{prior.get('checkType', 'incident')} ({prior.get('resource', 'capacity')})"
        card = build_card("resolved", title=title)
        dispatch_outbound("tier2_alert", {"attachments": [card]}, sinks=delivery_sinks)
        alerts_store["resolve"](key, now_iso)
        actions["resolved"].append(key)

    return actions


def run_tier2_check(collector, *, delivery_sinks=None, findings_store=None,
                    heartbeat_store=None, config=None, tenant=None, scope=None,
                    alerts_store=None, reasoner=None, chat_writer=None, app_url="", now_dt=None):
    """Run one Tier 2 deterministic check. Zero LLM calls.

    ``collector``: a collector port ``{"collect": fn}`` — at minimum the Capacity Events collector.
    ``delivery_sinks``: reserved for Phase 10 (Entra bot identity); pass None for now.
    ``findings_store``: a ``{"query": fn}`` store for recurrence cross-reference (Phase 6).
    ``heartbeat_store``: a ``{"write": fn(timestamp)}`` store for self-observability (Task 9.4).
    ``config``: detection config (uses DEFAULT_CONFIG if None).

    Returns ``{"triggered": bool, "triggers": list, "delivered": dict, "checkedAt": str}``.
    """
    from ..config import DEFAULT_CONFIG
    config = config if config is not None else DEFAULT_CONFIG
    checked_at = _now_iso()

    if heartbeat_store is not None:
        try:
            heartbeat_store["write"](checked_at)
        except Exception:
            pass

    try:
        facts = collector["collect"]()
    except Exception as exc:
        print(f"[tier2] collector FAILED: {type(exc).__name__}: {exc}")
        return {"triggered": False, "triggers": [], "delivered": {},
                "checkedAt": checked_at, "error": "collector failed"}

    # Observability: what did the collector actually pull? (peakCuPct=None => no capacity data /
    # blind collector; a number => live data, and this is the live peak.)
    _cap = (facts or {}).get("capacity") or {}
    _items = (facts or {}).get("items") or []
    print(f"[tier2] pulled: peakCuPct={_cap.get('peakCuPct')} "
          f"throttleMinutes={_cap.get('throttleMinutes')} overageTotalMs={_cap.get('overageTotalMs')} "
          f"items={len(_items)}")

    triggers = []
    triggers.extend(_check_concentration(facts, config))
    triggers.extend(_check_throttle(facts))
    triggers.extend(_check_pressure(facts))
    triggers.extend(_check_overage(facts))
    triggers.extend(_check_data_availability(facts))

    triggers = _cross_reference_recurrence(triggers, findings_store,
                                           scope=scope, tenant=tenant)

    triggered = any(t.get("check") != "data_unavailable" for t in triggers)

    # Delivery: sub-project #2 wires the Tier-2 -> Teams alert path when the job provides a sink +
    # an alerts store (gated on TIER2_WEBHOOK_ENABLED upstream). Otherwise stays silent (no-op).
    delivered = {}
    if delivery_sinks and alerts_store is not None:
        try:
            delivered = process_alerts(
                triggers, alerts_store=alerts_store, delivery_sinks=delivery_sinks,
                reasoner=reasoner, chat_writer=chat_writer, app_url=app_url, now_dt=now_dt)
        except Exception as exc:  # delivery must never crash the deterministic check
            delivered = {"error": f"{type(exc).__name__}: {exc}"}

    return {"triggered": triggered, "triggers": triggers,
            "delivered": delivered, "checkedAt": checked_at}
