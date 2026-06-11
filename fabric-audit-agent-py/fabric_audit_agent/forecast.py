"""Forecast the peak-CU trend from the run-metric series. Port of ``core/forecast.js``. Pure."""
import math


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fmt(x):
    return str(int(x)) if x == int(x) else str(x)


def _slope_of(ys):
    """Least-squares slope of y over index 0..n-1."""
    n = len(ys)
    if n < 2:
        return 0
    mx = (n - 1) / 2
    my = sum(ys) / n
    num = den = 0.0
    for i in range(n):
        num += (i - mx) * (ys[i] - my)
        den += (i - mx) ** 2
    return 0 if den == 0 else num / den


def forecast_capacity(history=None, ceiling=100, min_points=3):
    history = history or []
    series = [v for v in ((h.get("metrics") or {}).get("peakCuPct") for h in history if isinstance(h, dict)) if _is_num(v)]
    if len(series) < min_points:
        return {"trend": "insufficient-data", "points": len(series)}

    slope = _slope_of(series)
    current = series[-1]
    trend = "rising" if slope > 0.5 else ("falling" if slope < -0.5 else "flat")
    runs_to_ceiling = math.ceil((ceiling - current) / slope) if (slope > 0 and current < ceiling) else None
    slope_per_run = math.floor(slope * 10 + 0.5) / 10
    if runs_to_ceiling is not None:
        message = f"At current trend (+{_fmt(slope_per_run)}%/run), peak CU reaches {ceiling}% in ~{runs_to_ceiling} run(s)."
    else:
        message = f"Peak CU trend is {trend}; no ceiling breach projected."
    return {"trend": trend, "points": len(series), "current": current, "slopePerRun": slope_per_run, "runsToCeiling": runs_to_ceiling, "message": message}
