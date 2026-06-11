"""Format an audit envelope into a Teams-style card payload. Port of ``core/teams-card.js``.

Representative shape; the exact Adaptive Card schema is finalized at deployment. Pure.
"""


def _js_str(v):
    """Mirror JS ``String(x)`` for the missing-value case: a missing dict key is the
    analog of JS ``undefined``, so None → ``"undefined"`` (Node renders the literal text
    and never throws), not Python's ``"None"``."""
    return "undefined" if v is None else str(v)


def _fix0(f):
    fix = f.get("fix")
    val = fix[0] if isinstance(fix, list) and fix else None
    return val if val is not None else "see report"


def build_teams_card(envelope):
    envelope = envelope or {}
    d = envelope.get("data") or {}
    findings = d.get("findings") or []
    criticals = [f for f in findings if (f.get("score") or {}).get("level") == "Critical"]
    verdict = d.get("verdict")
    summary = envelope.get("summary")

    sections = [{"heading": "Summary", "text": summary if summary is not None else ""}]
    if verdict:
        sections.append({"heading": "Capacity verdict", "text": f"{_js_str(verdict.get('decision')).upper()} — {_js_str(verdict.get('reason'))}"})
    sections.append({
        "heading": f"Critical findings ({len(criticals)})",
        "items": [f"{_js_str(f.get('what'))} — Fix: {_fix0(f)}" for f in criticals[:10]],
    })
    return {"type": "message", "summary": summary if summary is not None else "Fabric audit", "sections": sections}
