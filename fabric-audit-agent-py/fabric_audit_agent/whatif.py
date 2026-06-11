"""Project the capacity impact of a proposed asset. Port of ``core/whatif.js``. Pure."""
from .config import DEFAULT_CONFIG


def assess_what_if(facts=None, proposed=None, config=None):
    facts = facts or {}
    proposed = proposed or {}
    config = config or DEFAULT_CONFIG
    c = facts.get("capacity") or {}
    cap = config["capacity"]
    impacts = []
    risk = 0

    if proposed.get("refreshAt"):
        same_window = [r for r in (c.get("refreshes") or []) if r.get("scheduledAt") == proposed["refreshAt"]]
        if len(same_window) >= 1:
            impacts.append(f"Refreshing at {proposed['refreshAt']} joins {len(same_window)} existing refresh(es) — worsens contention.")
            risk += 2 if len(same_window) >= (cap["contentionMin"] - 1) else 1
    if proposed.get("kind") == "model" and (proposed.get("sizeGB") or 0) >= cap["oversizedGB"]:
        impacts.append(f"Proposed model is {proposed.get('sizeGB')} GB (>= {cap['oversizedGB']} GB oversized threshold).")
        risk += 1
    if (c.get("peakCuPct") or 0) >= cap["throttleWarnPct"]:
        impacts.append(f"Capacity {c.get('capacityId') or ''} already peaks at {c.get('peakCuPct')}% CU — little headroom for new load.".strip())
        risk += 2

    verdict = "blocked" if risk >= 4 else ("risky" if risk >= 2 else "safe")
    return {"proposed": proposed, "impacts": impacts, "riskScore": risk, "verdict": verdict}
