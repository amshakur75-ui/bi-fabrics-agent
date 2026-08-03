"""Severity scoring. Faithful port of the Node ``core/severity.js``.

Maps a detector flag {type, evidence} to {level, reason}. Pure. Config domains are
read lazily inside the relevant branch (matching the JS, which only touches the
domain it needs — so a partial config never fails an unrelated branch).
"""
from .config import DEFAULT_CONFIG


def score_severity(flag, config=None):
    config = config or DEFAULT_CONFIG
    e = flag.get("evidence") or {}
    t = flag.get("type")

    if t == "capacity.throttle":
        cap = config["capacity"]
        if e.get("peakCuPct", 0) >= cap["throttleCritPct"] and e.get("throttleMinutes", 0) > cap["throttleCritMinutes"]:
            return {"level": "Critical", "reason": f"CU peaked {e.get('peakCuPct')}% with {e.get('throttleMinutes')} min throttled"}
        return {"level": "Warning", "reason": f"CU peaked {e.get('peakCuPct')}%"}

    if t == "capacity.contention":
        n = len(e.get("datasets") or [])
        level = "Critical" if n >= config["capacity"]["contentionCritCount"] else "Warning"
        return {"level": level, "reason": f"{n} models refresh at {e.get('time')}"}

    if t == "capacity.oversized-model":
        if e.get("sizeGB", 0) >= (config["capacity"]["oversizedCritPct"] / 100) * e.get("memoryGB", 0):
            return {"level": "Critical", "reason": f"model {e.get('sizeGB')}GB vs {e.get('memoryGB')}GB capacity"}
        return {"level": "Warning", "reason": f"model {e.get('sizeGB')}GB on {e.get('memoryGB')}GB capacity"}

    if t == "capacity.concentration":
        level = "Critical" if e.get("sharePct", 0) >= config["capacity"]["concentrationCritPct"] else "Warning"
        share_label = "monitored CU" if e.get("attributionMode") == "cost" else "capacity CU"
        return {"level": level, "reason": f"{e.get('sharePct')}% of {share_label} in one item"}

    if t == "capacity.user-concentration":
        level = "Critical" if e.get("sharePct", 0) >= config["capacity"]["concentrationCritPct"] else "Warning"
        return {"level": level, "reason": f"{e.get('sharePct')}% of monitored CU by one user"}

    if t == "capacity.user-ranking":
        return {"level": "Info", "reason": "top CU consumers (none over threshold)"}

    if t == "model.bidirectional":
        level = "Critical" if e.get("count", 0) >= config["model"]["bidirectionalCritMin"] else "Warning"
        return {"level": level, "reason": f"{e.get('count')} bidirectional relationships"}

    if t == "model.auto-datetime":
        return {"level": "Warning", "reason": "Auto Date/Time inflates model size"}

    if t == "model.refresh-failing":
        level = "Critical" if e.get("failRatePct", 0) >= config["model"]["refreshFailCritPct"] else "Warning"
        return {"level": level, "reason": f"{e.get('failRatePct')}% refresh failures"}

    if t == "report.too-many-visuals":
        level = "Critical" if e.get("visuals", 0) >= config["report"]["visualsCritMin"] else "Warning"
        return {"level": level, "reason": f"{e.get('visuals')} visuals on one page"}

    if t == "report.directquery":
        return {"level": "Warning", "reason": "DirectQuery adds per-interaction query load"}

    if t == "report.slow-visual":
        level = "Critical" if e.get("ms", 0) >= config["report"]["slowVisualCritMs"] else "Warning"
        return {"level": level, "reason": f"visual renders in {e.get('ms')} ms"}

    if t == "pipeline.failing":
        if e.get("status") == "Failed":
            return {"level": "Critical", "reason": "last run failed"}
        return {"level": "Warning", "reason": f"{e.get('failRatePct')}% failure rate"}

    if t == "pipeline.gateway":
        return {"level": "Critical", "reason": "gateway unhealthy — refreshes will fail"}

    if t == "lineage.blast-radius":
        if e.get("affectedCount", 0) >= 1:
            return {"level": "Critical", "reason": f"{e.get('affectedCount')} downstream assets impacted"}
        return {"level": "Warning", "reason": "isolated failure, no downstream impact"}

    if t == "security.admin-grant":
        return {"level": "Critical", "reason": "admin role on a sensitive workspace"}
    if t == "security.external-share":
        return {"level": "Warning", "reason": "item shared outside the org"}
    if t == "security.unusual-access":
        level = "Critical" if e.get("ratio", 0) >= config["security"]["unusualCritRatio"] else "Warning"
        return {"level": level, "reason": f"{e.get('ratio')}x normal access rate"}
    if t == "cost.unused-report":
        return {"level": "Info", "reason": "0 views in 30 days"}
    if t == "cost.idle-capacity":
        return {"level": "Warning", "reason": f"{e.get('avgCuPct')}% average CU"}

    if t == "meta.detector-error":
        return {"level": "Warning", "reason": "a detector failed and was skipped"}

    return {"level": "Info", "reason": "unclassified"}
