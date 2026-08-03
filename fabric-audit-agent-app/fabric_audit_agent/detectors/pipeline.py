"""Pipeline/refresh-health detectors. Faithful port of the Node ``core/detectors/pipeline.js``."""
from ..config import DEFAULT_CONFIG


def detect_pipelines(facts, config=None):
    config = config or DEFAULT_CONFIG
    pipelines = (facts or {}).get("pipelines") or []
    thr = config["pipeline"]
    flags = []
    for p in pipelines:
        where = f"{p.get('workspace')} / {p.get('name')}"
        when = p.get("lastRunAt") or ""
        if p.get("lastStatus") == "Failed" or (p.get("failRatePct") or 0) >= thr["failRatePct"]:
            flags.append({
                "type": "pipeline.failing", "resource": where, "when": when,
                "evidence": {"status": p.get("lastStatus"), "failRatePct": p.get("failRatePct") or 0},
                "what": f"Pipeline \"{p.get('name')}\" last status {p.get('lastStatus')} (fail rate {p.get('failRatePct') or 0}%).",
            })
        if p.get("gatewayHealthy") is False:
            flags.append({
                "type": "pipeline.gateway", "resource": where, "when": when, "evidence": {},
                "what": f"Pipeline \"{p.get('name')}\" depends on an unhealthy gateway.",
            })
    return flags
