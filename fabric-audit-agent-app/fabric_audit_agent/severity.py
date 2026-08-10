"""Severity scoring. Faithful port of the Node ``core/severity.js``.

Maps a detector flag {type, evidence} to {level, reason}. Pure. Config domains are
read lazily inside the relevant branch (matching the JS, which only touches the
domain it needs — so a partial config never fails an unrelated branch).
"""
from .config import DEFAULT_CONFIG


def _num(v):
    """Numeric or None. Rejects bool/None/non-numeric, mirroring the repo's other numeric guards.

    Needed because `.get(k, 0)` defaults only a MISSING key: a key present with an explicit None
    (which several mappers emit) sails through and then raises inside a comparison.
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def score_severity(flag, config=None):
    config = config or DEFAULT_CONFIG
    e = flag.get("evidence") or {}
    t = flag.get("type")

    if t == "capacity.throttle":
        cap = config["capacity"]
        # `.get(k, 0)` only defaults a MISSING key -- a key present and explicitly None still yields
        # None, and `None > 30` raises. The REST mapper emits `c.get("throttledMinutes")`, i.e. None
        # whenever the payload omits it, while detectors/capacity.py still fires at peak >= 80. And
        # score_severity is called from the reasoners OUTSIDE detect_all's failure-isolation shell,
        # so that TypeError took out the ENTIRE finding batch, not just this one finding.
        peak = _num(e.get("peakCuPct"))
        mins = _num(e.get("throttleMinutes"))
        if (peak is not None and peak >= cap["throttleCritPct"]
                and mins is not None and mins > cap["throttleCritMinutes"]):
            return {"level": "Critical",
                    "reason": f"CU peaked {e.get('peakCuPct')}% with {e.get('throttleMinutes')} min throttled"}
        if peak is None:
            return {"level": "Warning", "reason": "CU peak unknown (no capacity reading)"}
        return {"level": "Warning", "reason": f"CU peaked {e.get('peakCuPct')}%"}

    if t == "capacity.contention":
        n = len(e.get("datasets") or [])
        level = "Critical" if n >= config["capacity"]["contentionCritCount"] else "Warning"
        return {"level": level, "reason": f"{n} models refresh at {e.get('time')}"}

    if t == "capacity.oversized-model":
        # The capacity memory figure is genuinely UNKNOWN on most paths: importers/map.py defaults
        # memoryGB to 0 when the export has no memory column (the Capacity Metrics timepoint export
        # has none), and the REST mapper passes None. With 0, the right-hand side collapses to 0 and
        # EVERY model over the 4 GB detector gate scored Critical "model 4GB vs 0GB capacity"
        # unconditionally; with None it raised. Unknown capacity cannot justify a Critical -- the
        # comparison is meaningless without a denominator, so say so instead of inventing a verdict.
        size = _num(e.get("sizeGB"))
        mem = _num(e.get("memoryGB"))
        if mem is None or mem <= 0:
            return {"level": "Warning",
                    "reason": f"model {e.get('sizeGB')}GB; capacity memory unknown, cannot size it"}
        if size is not None and size >= (config["capacity"]["oversizedCritPct"] / 100) * mem:
            return {"level": "Critical", "reason": f"model {e.get('sizeGB')}GB vs {e.get('memoryGB')}GB capacity"}
        return {"level": "Warning", "reason": f"model {e.get('sizeGB')}GB on {e.get('memoryGB')}GB capacity"}

    if t == "capacity.concentration":
        level = "Critical" if e.get("sharePct", 0) >= config["capacity"]["concentrationCritPct"] else "Warning"
        # No producer has EVER emitted the literal "cost": attribution_rollup emits "cost-cpu" /
        # "cost-duration" (the N7 split) and a "frequency" mode also exists. So this comparison was
        # always false and every live concentration finding's severity reason read
        # "48% of capacity CU in one item" for what is a CpuTimeMs/DurationMs PROXY -- the exact
        # claim gates.true_cu_per_user_gate marks PERMANENTLY BLOCKED. Mirrors the form already
        # fixed in detectors/concentration.py: only a MISSING mode may claim true capacity CU.
        share_label = "capacity CU" if e.get("attributionMode") is None else "monitored CU"
        return {"level": level, "reason": f"{e.get('sharePct')}% of {share_label} in one item"}

    if t == "capacity.user-ranking":
        return {"level": "Info", "reason": "top monitored-CU consumers (none over threshold)"}

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

    if t == "pattern.cross-workspace":   # B4: a systemic pattern across N workspaces is material
        n = (e or {}).get("workspaceCount")
        return {"level": "Warning", "reason": f"same pattern in {n} workspaces" if n else "systemic pattern"}

    if t == "activity.slow-operation":
        return {"level": "Warning", "reason": f"operation took {e.get('durationSeconds')}s"}
    if t == "activity.recurring-shape":
        return {"level": "Warning", "reason": f"same query shape recurred {e.get('occurrences')}x"}
    if t == "activity.long-running-cluster":
        return {"level": "Warning", "reason": f"{e.get('count')} long-running operations on one item"}
    if t == "activity.user-baseline-deviation":
        # Level depends on WHICH fallback layer produced the baseline. A personalized p95 (the
        # user's own 14-day history) is a real per-user anomaly -> Warning. The estate-wide
        # fallback is a coarse cross-user comparison for someone with no history yet, so it is
        # Info: worth surfacing as context, not worth paging on.
        # Without this branch the type fell through to {"level": "Info", "reason": "unclassified"},
        # which `sweep_delivery` then drops at SWEEP_MIN_LEVEL=Warning as `skipped_minor` — the
        # finding would look wired and be invisible.
        src = (e or {}).get("baselineSource")
        ratio = (e or {}).get("ratioVsP95")
        detail = f"{ratio}x their own 14-day p95" if ratio else "above their own 14-day p95"
        if src == "personalized":
            return {"level": "Warning", "reason": detail}
        return {"level": "Info",
                "reason": (f"{ratio}x the estate-wide p95 (no personalized baseline yet)"
                           if ratio else "above the estate-wide p95 (no personalized baseline yet)")}

    if t == "query.mdx-crossjoin":
        return {"level": "Warning", "reason": "heavy MDX matrix cross-join shape recurring"}
    if t == "query.dax-antipattern":
        return {"level": "Warning", "reason": "recurring DAX anti-pattern"}

    if t == "refresh.credential":
        return {"level": "Critical", "reason": "refresh failing on an expired/invalid credential"}
    if t == "refresh.gateway":
        return {"level": "Warning", "reason": "refresh failing on a gateway problem"}
    if t == "refresh.timeout":
        return {"level": "Warning", "reason": "refresh failing on a source query timeout"}
    if t == "refresh.concurrency":
        return {"level": "Warning", "reason": "refresh failing on a concurrency/capacity limit"}
    if t == "refresh.constraint":
        return {"level": "Warning", "reason": "refresh failing on a data/constraint violation"}
    if t == "refresh.chronic":
        return {"level": "Warning", "reason": f"same refresh error {e.get('count')}x — a chronic pattern"}
    if t == "refresh.retry-storm":
        return {"level": "Warning", "reason": f"refresh retried {e.get('attempts')} times"}
    if t == "refresh.failing":
        return {"level": "Warning", "reason": f"refresh failed with {e.get('errorCode')}"}
    if t == "refresh.slow-phase":
        return {"level": "Warning", "reason": f"Data phase took {e.get('minutes')} minutes — a slow refresh phase"}

    if t == "xmla.auth":
        return {"level": "Warning", "reason": "XMLA endpoint authentication/token failure"}
    if t == "xmla.bad-request":
        return {"level": "Warning", "reason": "Bad Request on a TMSL/XMLA command"}
    if t == "xmla.timeout":
        return {"level": "Warning", "reason": "XML for Analysis request timed out"}
    if t == "xmla.connection-drop":
        return {"level": "Warning", "reason": "XMLA connection dropped/reset"}

    return {"level": "Info", "reason": "unclassified"}
