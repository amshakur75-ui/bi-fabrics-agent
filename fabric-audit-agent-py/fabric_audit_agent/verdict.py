"""Capacity verdict. Faithful port of the Node ``core/verdict.js``.

Decides whether the capacity needs optimization first or a genuine size-up, from the
capacity facts + capacity-domain flags.
"""
_NEXT_SKU = {
    "F2": "F4", "F4": "F8", "F8": "F16", "F16": "F32",
    "F32": "F64", "F64": "F128", "F128": "F256", "F256": "F512",
}


def build_capacity_verdict(facts, flags):
    c = (facts or {}).get("capacity")
    if not c:
        return {"decision": "unknown", "reason": "No capacity telemetry available.", "evidence": {}}

    cap_flags = [f for f in (flags or []) if str(f.get("type", "")).startswith("capacity.")]
    throttling = any(f.get("type") == "capacity.throttle" for f in cap_flags)
    if not throttling:
        return {
            "decision": "healthy",
            "reason": f"Capacity {c.get('capacityId')} peaked at {c.get('peakCuPct')}% CU — within limits.",
            "evidence": {"peakCuPct": c.get("peakCuPct")},
        }

    optimizations = [f["type"] for f in cap_flags if f.get("type") in ("capacity.contention", "capacity.oversized-model")]
    if optimizations:
        return {
            "decision": "optimize",
            "reason": f"Capacity is throttling, but {len(optimizations)} optimization(s) remain — fix these before paying for a bigger SKU.",
            "evidence": {"peakCuPct": c.get("peakCuPct"), "throttleMinutes": c.get("throttleMinutes"), "optimizations": optimizations},
        }

    return {
        "decision": "size-up",
        "reason": f"Capacity {c.get('capacityId')} is throttling with no remaining optimizations — the honest answer is a larger SKU.",
        "evidence": {
            "peakCuPct": c.get("peakCuPct"), "throttleMinutes": c.get("throttleMinutes"),
            "currentSku": c.get("sku"), "recommendedSku": _NEXT_SKU.get(c.get("sku"), "next tier up"),
        },
    }
