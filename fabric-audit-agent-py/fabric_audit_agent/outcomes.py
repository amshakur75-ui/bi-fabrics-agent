"""Run-over-run outcomes: what got resolved + how the metric moved. Port of ``core/outcomes.js``. Pure."""
import math


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _r1(x):
    return math.floor(x * 10 + 0.5) / 10


def assess_outcomes(current_findings=None, history=None, current_metric=None):
    current_findings = current_findings or []
    history = history or []
    if not history:
        return {"resolvedSinceLast": [], "metricDelta": None}
    prev = history[-1]
    # preserve prev.findings order (matches JS Set insertion order)
    prev_active, seen = [], set()
    for f in (prev.get("findings") or []):
        k = f.get("key")
        if not f.get("suppressed") and k and k not in seen:
            seen.add(k)
            prev_active.append(k)
    cur = {f.get("key") for f in current_findings}
    resolved_since_last = [k for k in prev_active if k not in cur]

    metric_delta = None
    frm = (prev.get("metrics") or {}).get("peakCuPct")
    if _is_num(frm) and _is_num(current_metric):
        metric_delta = {
            "metric": "peakCuPct", "from": frm, "to": current_metric,
            "change": _r1(current_metric - frm), "improved": current_metric < frm,
        }
    return {"resolvedSinceLast": resolved_since_last, "metricDelta": metric_delta}


def summarize_outcomes(outcomes=None):
    outcomes = outcomes or {"resolvedSinceLast": [], "metricDelta": None}
    parts = []
    if outcomes["resolvedSinceLast"]:
        parts.append(f"{len(outcomes['resolvedSinceLast'])} finding(s) resolved since the last run")
    if outcomes.get("metricDelta"):
        d = outcomes["metricDelta"]
        parts.append(f"peak CU {'improved' if d['improved'] else 'rose'} {d['from']}% → {d['to']}%")
    return "; ".join(parts)
