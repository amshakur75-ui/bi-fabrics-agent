"""Run a read-only audit. Port of ``core/pipeline.js``.

All I/O is injected via ports (dict-style: ``{"method": callable}``, matching the stub
reasoner), so the same core runs against mock or real adapters. Sync (the stub + adapters
are synchronous in Python).
"""
from datetime import datetime, timezone

from .detectors import detect_all
from .validate import validate_facts
from .finding import wrap_envelope
from .automation.dedupe import dedupe
from .automation.escalate import apply_escalation
from .automation.trend import annotate_recurring
from .automation.digest import build_digest
from .verdict import build_capacity_verdict
from .coaching import get_user_tip
from .lifecycle import apply_lifecycle
from .config import DEFAULT_CONFIG
from .health_score import build_health_score
from .roadmap import build_roadmap
from .accountability import annotate_accountability, summarize_accountability
from .sla import assess_sla, summarize_sla
from .forecast import forecast_capacity
from .outcomes import assess_outcomes
from .anomaly import detect_anomalies
from .correlate import correlate
from .stagger import plan_stagger
from .routing import route_findings
from .audience import view_for
from .narrative import exec_narrative
from .confidence import score_confidence
from .run_log import build_run_log


def _parse_ms(s):
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000


def _summarize(findings):
    crit = len([f for f in findings if f["score"]["level"] == "Critical"])
    warn = len([f for f in findings if f["score"]["level"] == "Warning"])
    return f"Audit complete: {len(findings)} findings ({crit} critical, {warn} warning)."


def _type_of(f):
    k = f.get("key")
    return k.split("::")[0] if isinstance(k, str) else None


def run_audit(collector, reasoner, delivery, store=None, lifecycle_store=None,
              agent_id=None, now=None, config=None, tenant=None):
    config = config or DEFAULT_CONFIG
    run_at = now if now is not None else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    now_ms = _parse_ms(run_at)
    now_ms = now_ms if now_ms is not None else 0

    facts = collector["collect"]()
    validation = validate_facts(facts)
    resolved_tenant = tenant
    if resolved_tenant is None:
        resolved_tenant = (facts.get("capacity") or {}).get("tenant")
    if resolved_tenant is None:
        resolved_tenant = "default"
    flags = detect_all(facts, config)
    findings = dedupe(reasoner["reason"](facts, flags))

    suppressed = []
    if lifecycle_store:
        states = lifecycle_store["load"]()
        split = apply_lifecycle(findings, states, now_ms)
        findings = split["active"]
        suppressed = split["suppressed"]

    digest = forecast = outcomes = None
    anomalies = []
    peak = (facts.get("capacity") or {}).get("peakCuPct")

    if store:
        history = store["history"]()
        findings = apply_escalation(findings, history)
        findings = annotate_recurring(findings, history)
        findings = annotate_accountability(findings, history)
        findings = assess_sla(findings, history, now_ms)
        digest = build_digest(findings, history)
        forecast = forecast_capacity([*history, {"metrics": {"peakCuPct": peak}}])
        outcomes = assess_outcomes(findings, history, peak)
        anomalies = detect_anomalies(facts, history)
        store["append"]({
            "runAt": run_at,
            "tenant": resolved_tenant,
            "metrics": {"peakCuPct": peak},
            "findings": [
                *[{"key": f.get("key"), "level": f["score"]["level"], "where": f.get("where"), "what": f.get("what"), "suppressed": False} for f in findings],
                *[{"key": f.get("key"), "level": f["score"]["level"], "where": f.get("where"), "what": f.get("what"), "suppressed": True} for f in suppressed],
            ],
        })

    # User coaching — attach an author-facing tip where one applies.
    coached = []
    for f in findings:
        t = _type_of(f)
        tip = get_user_tip(t) if t else None
        coached.append({**f, "userTip": tip} if tip else f)
    findings = coached

    # Confidence — deterministic detections = high; Claude-enriched = medium; meta/errors = low.
    findings = [{**f, "confidence": score_confidence(f)} for f in findings]

    verdict = build_capacity_verdict(facts, flags)
    health_score = build_health_score(findings)
    roadmap = build_roadmap(findings)
    correlations = correlate(findings)

    envelope = wrap_envelope(agent_id=agent_id, findings=findings, summary=_summarize(findings))
    d = envelope["data"]
    d["tenant"] = resolved_tenant
    d["verdict"] = verdict
    if digest:
        d["digest"] = digest
    accountability = summarize_accountability(findings)
    if accountability["ignoredCount"] > 0:
        d["accountability"] = accountability
    sla = summarize_sla(findings)
    if sla["breachedCount"] > 0:
        d["sla"] = sla
    d["healthScore"] = health_score
    d["roadmap"] = roadmap
    if correlations:
        d["correlations"] = correlations
    stagger_plan = plan_stagger(facts)
    if stagger_plan:
        d["staggerPlan"] = stagger_plan
    routing = route_findings(findings)
    if routing:
        d["routing"] = routing
    if forecast and forecast.get("runsToCeiling") is not None:
        d["forecast"] = forecast
    if outcomes and (outcomes["resolvedSinceLast"] or outcomes["metricDelta"]):
        d["outcomes"] = outcomes
    if anomalies:
        d["anomalies"] = anomalies
    if suppressed:
        d["suppressed"] = [{"key": f.get("key"), "state": f["lifecycle"]["state"], "what": f.get("what")} for f in suppressed]
    if validation["issues"]:
        d["dataQuality"] = validation["issues"]
    d["narrative"] = exec_narrative(view_for(envelope, "exec"))
    d["runLog"] = build_run_log(facts, envelope, run_at)
    delivery["deliver"](envelope)
    return envelope
