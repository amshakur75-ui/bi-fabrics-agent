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
    delivered = result.get("delivered", True) if isinstance(result, dict) else True
    return {"dispatched": True, "delivered": bool(delivered), "actionType": action_type,
            "disclosure": line, "reason": None}
