"""Non-fatal facts validation. Port of ``core/validate.js``.

Missing domains are fine (just not audited); present-but-malformed shapes are reported.
"""
_REQUIRED_CAPACITY = ["capacityId", "sku", "memoryGB", "peakCuPct"]
_ARRAY_DOMAINS = ["models", "reports", "pipelines"]


def validate_facts(facts=None):
    facts = facts or {}
    issues = []
    cap = facts.get("capacity")
    if cap is not None:   # JS `if (facts.capacity)` — any object (incl. {}) enters
        for k in _REQUIRED_CAPACITY:
            if k not in cap:   # JS `=== undefined` (explicit null would not flag)
                issues.append({"domain": "capacity", "issue": f"missing {k}"})
        if "refreshes" in cap and not isinstance(cap["refreshes"], list):
            issues.append({"domain": "capacity", "issue": "refreshes must be an array"})
    for d in _ARRAY_DOMAINS:
        if d in facts and not isinstance(facts[d], list):
            issues.append({"domain": d, "issue": "expected an array"})
    if facts.get("lineage") and not isinstance(facts["lineage"].get("nodes"), list):
        issues.append({"domain": "lineage", "issue": "nodes must be an array"})
    return {"ok": len(issues) == 0, "issues": issues}
