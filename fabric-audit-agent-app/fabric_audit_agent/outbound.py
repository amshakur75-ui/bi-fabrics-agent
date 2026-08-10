"""Typed outbound-action allowlist (Phase 6; the 5.3-C plumbing item).

Outbound is CLOSED BY CONSTRUCTION: only a registered, ENABLED action type can send, and every
payload is routed through the Phase 5.2 egress gate before any sink emits. Nothing data-mutating is
registrable — the agent surfaces findings, it never acts, writes, scales, or remediates.

All delivery action types (teams_notify, email_notify) have been removed — Phase 10 (Entra bot
identity) will provide real delivery from scratch. ``ado_create_ticket`` remains registered-but-
DISABLED as a Phase 10 placeholder.
"""
from .egress import apply_egress_controls, disclosure_line

_ALLOWLIST = {
    "ado_create_ticket": {"enabled": False, "sink": "ticket"},  # Phase 10 (Entra bot identity)
    # Tier-2 interim alerting (sub-project #2): read-only notification — surfaces a finding to a
    # Teams channel via a webhook sink. NOT data-mutating. Still gated: only fires when the job
    # actually provides a "webhook" sink (job wires it only when TIER2_WEBHOOK_ENABLED + secret set).
    "tier2_alert": {"enabled": True, "sink": "webhook"},
    # Daily capacity digest (Step 10): a once-a-day read-only summary card. Same posture as
    # tier2_alert — a notification, never data-mutating — and only fires when the daily-summary job
    # actually wires a "webhook" sink (DAILY_SUMMARY_ENABLED + POWER_AUTOMATE_ALERT_URL set).
    "daily_summary": {"enabled": True, "sink": "webhook"},
}


def _refuse(action_type, reason):
    return {"dispatched": False, "delivered": False, "actionType": action_type,
            "disclosure": None, "reason": reason}


def dispatch_outbound(action_type, payload, *, sinks):
    """Gate *payload* and hand it to the sink for *action_type*, iff that type is allowed+enabled.

    Returns ``{"dispatched": bool, "delivered": bool, "actionType": str, "disclosure": str|None,
    "reason": str|None}``. ``dispatched`` = the payload passed the allowlist+gate and reached the
    sink; ``delivered`` = the sink reported an actual send (a sink may no-op when unconfigured, e.g.
    email without SMTP set). A refusal (unknown/disabled type, or missing sink) never raises and
    never sends. Runtime configured-vs-inert is the sink's responsibility.
    """
    spec = _ALLOWLIST.get(action_type)
    if spec is None:
        return _refuse(action_type, "unknown action type")
    if not spec["enabled"]:
        return _refuse(action_type, "action type disabled (deferred to Phase 10)")
    sink = (sinks or {}).get(spec["sink"])
    if sink is None:
        return _refuse(action_type, "no sink provided")

    safe, meta = apply_egress_controls(payload, sink="alert")
    line = disclosure_line(meta)
    if line and isinstance(safe, dict):
        safe["summary"] = f"{(safe.get('summary') or '').rstrip()} {line}".strip()
    result = sink["deliver"](safe)
    # Fail CLOSED on the delivery REPORT. Defaulting to True meant a sink that returned a
    # non-dict (or a dict without the key) was recorded as a successful delivery, so an
    # undelivered capacity card was indistinguishable from a delivered one -- and it defeated
    # every caller that inspects `delivered` to decide whether to retry. "We do not know whether
    # this reached a human" must read as NOT delivered, never as delivered.
    delivered = result.get("delivered", False) if isinstance(result, dict) else False
    return {"dispatched": True, "delivered": bool(delivered), "actionType": action_type,
            "disclosure": line, "reason": None}
