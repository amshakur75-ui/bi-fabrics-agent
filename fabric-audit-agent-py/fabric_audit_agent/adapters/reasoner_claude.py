"""Claude ReasonerPort. Port of ``adapters/reasoner.claude.js``.

The Anthropic-shaped client is injected (``client.messages.create(...) -> resp`` with
``resp.content[0].text``) so it's testable offline and swaps to the real ``anthropic`` SDK
(or a Databricks-hosted Claude endpoint) at deploy — see ``adapters.clients``.

Falls back to KB remediation on ANY API/parse error: the audit never fails because the LLM
is unavailable. Evidence sent to the model is sanitized (no names) by ``sanitize``.
"""
import json

from ..severity import score_severity
from ..kb import get_remediation
from ..finding import create_finding
from ..sanitize import sanitize
from ..config import DEFAULT_CONFIG

DEFAULT_MODEL = "claude-sonnet-4-6"

_SYSTEM_TEXT = (
    "You are a Microsoft Fabric / Power BI performance expert. "
    "You receive a JSON array of detected issues; each has an id, a type, and sanitized "
    "numeric evidence (no names). "
    'For EACH issue return an object {"id","why","impact","fix"}: why = one-sentence root '
    "cause; impact = one sentence; fix = array of 2-4 concrete remediation steps. "
    "Respond with ONLY a JSON array. No prose, no markdown fences."
)

# Prompt caching: system as content blocks with cache_control (mirrors the Node adapter).
# The static system block is stable across requests, maximising cache hits.
_SYSTEM = [{"type": "text", "text": _SYSTEM_TEXT, "cache_control": {"type": "ephemeral"}}]


def _extract_json_array(text):
    s = text.find("[")
    e = text.rfind("]")
    return text[s:e + 1] if (s >= 0 and e >= s) else "[]"


def _first_text(resp):
    """``resp?.content?.[0]?.text ?? '[]'`` — tolerant of a dict or an SDK object."""
    content = resp.get("content") if isinstance(resp, dict) else getattr(resp, "content", None)
    if not content:
        return "[]"
    block = content[0]
    text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
    return text if text is not None else "[]"


def create_claude_reasoner(client, model=DEFAULT_MODEL, config=None, max_flags=50):
    config = config if config is not None else DEFAULT_CONFIG

    def reason(facts, flags):
        if not flags:
            return []
        sanitized = sanitize(flags[:max_flags])

        enriched = []
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=1024,
                system=_SYSTEM,
                messages=[{"role": "user", "content": json.dumps(sanitized)}],
            )
            enriched = json.loads(_extract_json_array(_first_text(resp)))
        except Exception:
            # Network error, API error, or JSON parse failure — fall back to KB below.
            enriched = []

        by_id = {}
        for e in enriched:
            if isinstance(e, dict):
                by_id[e.get("id")] = e

        out = []
        for i, flag in enumerate(flags):
            e = by_id.get(i) or {}
            kb = get_remediation(flag["type"])
            e_fix = e.get("fix")
            fix = e_fix if (isinstance(e_fix, list) and e_fix) else kb["fixes"]
            finding = create_finding({
                "what": flag.get("what"),
                "where": flag.get("resource"),
                "when": flag.get("when"),
                "why": e.get("why") if e.get("why") is not None else kb["rootCause"],
                "impact": e.get("impact") if e.get("impact") is not None else "Impact not assessed.",
                "fix": fix,
                "score": score_severity(flag, config),
            })
            finding["key"] = f'{flag["type"]}::{flag["resource"]}'
            if e.get("why"):
                finding["reasonedBy"] = "claude"
            out.append(finding)
        return out

    return {"reason": reason}
