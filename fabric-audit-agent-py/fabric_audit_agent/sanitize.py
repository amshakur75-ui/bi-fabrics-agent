"""Sanitize evidence before it leaves the tenant (e.g. to an LLM). Port of ``core/sanitize.js``.

Keep numbers/booleans + a few safe enum strings; arrays -> count; drop identifying strings.
"""
_SAFE_STRING_KEYS = {"sku", "status", "time"}   # enum-like, not identifying


def sanitize_evidence(evidence=None):
    evidence = evidence or {}
    if evidence.get("sensitive") is True or evidence.get("sensitivityLabel"):
        return {"redacted": True}
    out = {}
    for k, v in evidence.items():
        if isinstance(v, (int, float)):        # numbers + booleans (bool is an int subclass, mirrors JS number||boolean)
            out[k] = v
        elif isinstance(v, list):
            out[f"{k}Count"] = len(v)
        elif isinstance(v, str) and k in _SAFE_STRING_KEYS:
            out[k] = v
        # else: drop (dataset names, sources, free text, timestamps)
    return out


def sanitize(flags):
    """External-safe payload: index + flag type + sanitized numeric evidence only."""
    return [{"id": i, "type": f.get("type"), "evidence": sanitize_evidence(f.get("evidence"))} for i, f in enumerate(flags)]
