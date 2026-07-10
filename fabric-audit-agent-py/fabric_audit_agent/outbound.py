"""Typed outbound-action allowlist (Phase 6; the 5.3-C plumbing item).

Outbound is CLOSED BY CONSTRUCTION: only a registered, ENABLED action type can send, and every
payload is routed through the Phase 5.2 egress gate before any sink emits. Nothing data-mutating is
registrable — the agent surfaces findings, it never acts, writes, scales, or remediates.

``teams_notify`` / ``ado_create_ticket`` are registered-but-DISABLED placeholders that light up in
Phase 7 (admin-consent channels). Flipping ``enabled`` is a deliberate, reviewed change — and even
then nothing sends unless the caller also supplies the matching sink, so a stray flag alone is inert.
"""
from .egress import apply_egress_controls, disclosure_line

# Static registry. ``enabled`` is a property of the ACTION TYPE, not of runtime configuration:
# whether a configured sink actually exists (e.g. SMTP env is set) is the SINK's own concern, so
# there is exactly one source of truth for "can this send right now". No write/scale/refresh type
# may ever appear here — outbound is surface-only.
_ALLOWLIST = {
    "email_notify": {"enabled": True, "sink": "email"},
    "teams_notify": {"enabled": False, "sink": "teams"},        # -> Phase 7
    "ado_create_ticket": {"enabled": False, "sink": "ticket"},  # -> Phase 7
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
        return _refuse(action_type, "action type disabled (deferred to Phase 7)")
    sink = (sinks or {}).get(spec["sink"])
    if sink is None:
        return _refuse(action_type, "no sink provided")

    # Egress chokepoint: gate BEFORE the sink sees the payload, and carry the disclosure into the
    # delivered content's summary (mirrors pipeline.run_audit / job._write_outputs / job._alert_failure)
    # so a capped/redacted alert says so instead of silently dropping findings.
    safe, meta = apply_egress_controls(payload, sink="alert")
    line = disclosure_line(meta)
    if line and isinstance(safe, dict):
        safe["summary"] = f"{(safe.get('summary') or '').rstrip()} {line}".strip()
    result = sink["deliver"](safe)
    # A sink that returns a {"delivered": ...} status (email) reports whether it actually sent;
    # a sink that returns nothing is assumed to have delivered.
    delivered = result.get("delivered", True) if isinstance(result, dict) else True
    return {"dispatched": True, "delivered": bool(delivered), "actionType": action_type,
            "disclosure": line, "reason": None}
