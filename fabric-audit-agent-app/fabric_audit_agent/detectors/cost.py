"""Cost / unused-resource detectors. Faithful port of the Node ``core/detectors/cost.js``."""
from ..config import DEFAULT_CONFIG


def detect_cost(facts, config=None):
    config = config or DEFAULT_CONFIG
    u = (facts or {}).get("usage") or {}
    cost = config["cost"]
    flags = []

    for r in (u.get("reports") or []):
        if (r.get("views30d") or 0) == 0:
            flags.append({
                "type": "cost.unused-report",
                "resource": f"{r.get('workspace')} / {r.get('name')}",
                "when": "",
                "evidence": {"views30d": 0},
                "what": f"Report \"{r.get('name')}\" has had 0 views in 30 days.",
            })

    for c in (u.get("capacities") or []):
        avg = c.get("avgCuPct")
        avg = avg if avg is not None else 100   # JS: c.avgCuPct ?? 100 (nullish, so 0 stays 0)
        if avg < cost["idleCuPct"]:
            flags.append({
                "type": "cost.idle-capacity",
                "resource": f"capacity {c.get('id')}",
                "when": "",
                "evidence": {"sku": c.get("sku"), "avgCuPct": c.get("avgCuPct")},
                "what": f"Capacity {c.get('id')} ({c.get('sku')}) averaged {c.get('avgCuPct')}% CU — largely idle.",
            })
    return flags
