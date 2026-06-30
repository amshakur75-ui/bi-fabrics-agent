"""Per-entity baselines: distribution (percentiles) + operation mix + peak hour, and a today-vs-baseline
comparison. Pure. Answers "is today abnormal vs this user's own history" — the CPU×duration model."""
import math


def _percentile(sorted_vals, pct):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = pct / 100.0 * (len(sorted_vals) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_vals[lo]
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def compute_baseline(rows):
    rows = rows or []
    cus = sorted(float(r.get("cuSeconds") or 0) for r in rows)
    op_mix = {}
    hours = {}
    for r in rows:
        op = r.get("operation")
        if op:
            op_mix[op] = op_mix.get(op, 0) + 1
        h = r.get("hourUtc")
        if h is not None:
            hours[h] = hours.get(h, 0) + 1
    peak_hour = max(hours, key=hours.get) if hours else None
    return {
        "count": len(cus),
        "p50": _percentile(cus, 50), "p95": _percentile(cus, 95), "p99": _percentile(cus, 99),
        "opMix": op_mix, "peakHourUtc": peak_hour,
    }


def compare_to_baseline(today_cu, baseline):
    cus_count = baseline.get("count") or 0
    p50 = baseline.get("p50")
    p95 = baseline.get("p95")
    today_cu = float(today_cu or 0)
    if not cus_count or p50 is None:
        return {"percentileRank": None, "deltaVsP50Pct": None, "shifted": False}
    # percentile rank of today vs the baseline cluster (rough: fraction at/below the p95 anchor)
    rank = 100.0 if (p95 is not None and today_cu >= p95) else (50.0 if today_cu >= p50 else 0.0)
    delta = ((today_cu - p50) / p50 * 100.0) if p50 else None
    shifted = bool(p95 is not None and today_cu > p95)
    return {"percentileRank": rank, "deltaVsP50Pct": delta, "shifted": shifted}
