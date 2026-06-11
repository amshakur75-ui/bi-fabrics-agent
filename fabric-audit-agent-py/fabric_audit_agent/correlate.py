"""Group related findings into root-cause clusters. Port of ``core/correlate.js``. Pure."""


def correlate(findings=None):
    findings = findings or []

    def has(prefix):
        return [f for f in findings if isinstance(f.get("key"), str) and f["key"].startswith(prefix)]

    correlations = []

    throttle = has("capacity.throttle")
    drivers = has("capacity.contention") + has("capacity.oversized-model")
    if throttle and drivers:
        correlations.append({
            "theme": "capacity-pressure",
            "findingKeys": [f["key"] for f in (throttle + drivers)],
            "narrative": f"Capacity throttling is likely driven by {len(drivers)} optimization issue(s) — resolve those before sizing up the SKU.",
        })

    model_fail = has("model.refresh-failing")
    pipe_fail = has("pipeline.failing")
    if model_fail and pipe_fail:
        correlations.append({
            "theme": "refresh-chain",
            "findingKeys": [f["key"] for f in (model_fail + pipe_fail)],
            "narrative": f"Refresh failures span {len(model_fail)} model(s) and {len(pipe_fail)} pipeline(s) — likely a shared gateway/source. Investigate the upstream together.",
        })

    sec = has("security.")
    if len(sec) >= 2:
        correlations.append({
            "theme": "security-cluster",
            "findingKeys": [f["key"] for f in sec],
            "narrative": f"{len(sec)} security/access findings detected together — handle as one access-review action.",
        })

    return correlations
