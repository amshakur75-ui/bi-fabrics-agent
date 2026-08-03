"""Deterministic leadership narrative. Port of ``core/narrative.js``. Pure."""

_VERDICT_TEXT = {
    "size-up": "a capacity increase is warranted",
    "optimize": "optimization opportunities remain before any capacity increase",
    "healthy": "capacity is healthy",
    "unknown": "capacity status is unknown",
}


def exec_narrative(exec_view=None):
    v = exec_view or {}
    health = v.get("health")
    health = health if health is not None else "—"
    critical = v.get("critical")
    critical = critical if critical is not None else 0
    warning = v.get("warning")
    warning = warning if warning is not None else 0
    parts = [
        f"Estate health is {health}/100 with {critical} critical and {warning} warning finding(s).",
        f"On capacity, {_VERDICT_TEXT.get(v.get('verdict'), 'status is unclear')}.",
    ]
    if v.get("accountability"):
        parts.append(f"{v['accountability']} issue(s) have been flagged repeatedly without resolution.")
    if v.get("topFindings"):
        parts.append(f"Top priority: {v['topFindings'][0]['what']}")
    return " ".join(parts)
