"""Forecast the peak-CU trend from the run-metric series. Port of ``core/forecast.js``. Pure.

Phase 4.1: the OLS slope now comes from the shared ``stats.linear_trend`` (single source of truth
with anomaly.py / analysis.ts) and the result carries an ``r2`` fit-quality figure plus a
``weakFit`` flag (R² < 0.3 on a non-flat trend) so a caller can caveat a poorly-fitted trend rather
than assert direction off any slope. The stricter ≥6-point / ±15%-band classification is available
via ``stats.trend_direction`` (surfaced here as ``directionStrict``); the legacy slope-threshold
``trend`` vocabulary is preserved so existing consumers (diagnose.py / forecast_throttle.py /
pipeline.py) are unaffected."""
import math

from . import stats


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fmt(x):
    return str(int(x)) if x == int(x) else str(x)


def forecast_capacity(history=None, ceiling=100, min_points=3):
    history = history or []
    series = [v for v in ((h.get("metrics") or {}).get("peakCuPct") for h in history if isinstance(h, dict)) if _is_num(v)]
    if len(series) < min_points:
        return {"trend": "insufficient-data", "points": len(series)}

    fit = stats.linear_trend(series)
    slope = fit["slope"]
    r2 = fit["r2"]
    current = series[-1]
    trend = "rising" if slope > 0.5 else ("falling" if slope < -0.5 else "flat")
    # Project only from a trend we are willing to CALL a trend. runs_to_ceiling was gated on
    # `slope > 0` while `trend` needs `slope > 0.5`, so a flat series projected a ceiling breach:
    # [60,60,60,60,60,60.2] rendered "At current trend (+0%/run), peak CU reaches 100% in ~1393
    # run(s)" -- a sentence that contradicts its own +0%/run in the same breath. And because
    # `weak_fit` required `trend != "flat"`, the "treat with caution" caveat was UNREACHABLE on
    # exactly the branch that needed it (that series has r2=0.429). pipeline.py surfaces the
    # forecast only when runsToCeiling is set, so the flat-but-projecting case was the dominant
    # shipped one. Both now key off the same trend decision.
    slope_per_run = math.floor(slope * 10 + 0.5) / 10
    projecting = trend == "rising" and current < ceiling
    runs_to_ceiling = math.ceil((ceiling - current) / slope) if projecting else None
    weak_fit = r2 < stats.WEAK_FIT_R2
    caveat = " (weak fit, treat with caution)" if weak_fit else ""
    if runs_to_ceiling is not None:
        message = f"At current trend (+{_fmt(slope_per_run)}%/run), peak CU reaches {ceiling}% in ~{runs_to_ceiling} run(s){caveat}."
    elif trend == "flat":
        message = (f"Peak CU is flat ({_fmt(slope_per_run)}%/run over {len(series)} runs); "
                   "no ceiling breach projected.")
    else:
        message = f"Peak CU trend is {trend}; no ceiling breach projected."
    return {"trend": trend, "points": len(series), "current": current,
            "slopePerRun": slope_per_run, "runsToCeiling": runs_to_ceiling,
            "r2": round(r2, 3), "weakFit": weak_fit,
            "directionStrict": stats.trend_direction(series)["direction"], "message": message}


def bucket_monthly_summary(history):
    """Monthly bucketed peak CU% for multi-month baseline comparisons."""
    from collections import defaultdict
    buckets = defaultdict(list)
    for run in (history or []):
        run_at = run.get("runAt", "")
        peak = (run.get("metrics") or {}).get("peakCuPct")
        if run_at and peak is not None:
            month = run_at[:7]
            buckets[month].append(peak)
    result = []
    for month in sorted(buckets):
        vals = buckets[month]
        result.append({
            "month": month,
            "meanPeakCuPct": round(sum(vals) / len(vals), 1),
            "maxPeakCuPct": round(max(vals), 1),
            "runCount": len(vals),
        })
    return result
