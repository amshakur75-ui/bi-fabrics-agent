"""Capacity detectors. Faithful port of the Node ``core/detectors/capacity.js``."""
from ..config import DEFAULT_CONFIG


def detect_capacity(facts, config=None):
    config = config or DEFAULT_CONFIG
    c = (facts or {}).get("capacity")
    if not c:
        return []
    cap = config["capacity"]
    flags = []

    # 1. Throttle risk
    if (c.get("peakCuPct") or 0) >= cap["throttleWarnPct"]:
        flags.append({
            "type": "capacity.throttle",
            "resource": f"{c.get('tenant')} / capacity {c.get('capacityId')}",
            "when": c.get("peakAt"),
            "evidence": {"peakCuPct": c.get("peakCuPct"), "throttleMinutes": c.get("throttleMinutes"), "sku": c.get("sku")},
            "what": f"Capacity {c.get('capacityId')} reached {c.get('peakCuPct')}% CU ({c.get('throttleMinutes')} min throttled).",
        })

    # 2. Refresh contention (>= contentionMin share a start time; a blank/unknown time can't prove simultaneity)
    by_time = {}
    for r in (c.get("refreshes") or []):
        t = str(r.get("scheduledAt") or "").strip()
        if not t:
            continue
        by_time.setdefault(t, []).append(r)
    for t, group in by_time.items():
        if len(group) >= cap["contentionMin"]:
            flags.append({
                "type": "capacity.contention",
                "resource": f"{c.get('tenant')} / capacity {c.get('capacityId')}",
                "when": t,
                "evidence": {"time": t, "datasets": [r.get("dataset") for r in group]},
                "what": f"{len(group)} datasets refresh simultaneously at {t}.",
            })

    # 3. Oversized model
    for r in (c.get("refreshes") or []):
        if (r.get("sizeGB") or 0) >= cap["oversizedGB"]:
            flags.append({
                "type": "capacity.oversized-model",
                "resource": f"{c.get('tenant')} / {r.get('workspace')} / {r.get('dataset')}",
                "when": r.get("scheduledAt"),
                "evidence": {"sizeGB": r.get("sizeGB"), "memoryGB": c.get("memoryGB"), "durationMin": r.get("durationMin")},
                "what": f"Model \"{r.get('dataset')}\" is {r.get('sizeGB')} GB and refreshes in {r.get('durationMin')} min.",
            })
    return flags
