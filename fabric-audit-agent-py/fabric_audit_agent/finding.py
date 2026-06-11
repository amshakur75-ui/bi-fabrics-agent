"""Finding construction + output envelope. Faithful port of the Node ``core/finding.js``.

A finding has exactly 7 fields: what, where, when, why, impact, fix, score.
``when`` may be an empty string (only None/missing is rejected — matches the JS
``=== undefined || === null`` check, so falsy values like "" and 0 are allowed).
"""
from datetime import datetime, timezone

REQUIRED = ("what", "where", "when", "why", "impact", "fix", "score")


def create_finding(parts):
    """Build a validated 7-field finding (dict). Raises on a missing required field."""
    for k in REQUIRED:
        if parts.get(k) is None:
            raise ValueError(f'create_finding: missing required field "{k}"')
    if not isinstance(parts["fix"], list):
        raise TypeError('create_finding: "fix" must be a list')
    return {k: parts[k] for k in REQUIRED}


def wrap_envelope(*, agent_id, findings, summary):
    """Wrap findings in the standard output envelope."""
    return {
        "success": True,
        "agent_id": agent_id,
        "data": {"findings": findings},
        "summary": summary,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
