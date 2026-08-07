"""Shared statistical primitives — the internally-consistent set ported from the KQL plugin's
``services/analysis.ts`` (master-integration-plan Phase 4.1-4.4 / tightening Part 21 HARDEN +
ADOPT METHOD). One source of truth so ``forecast.py``, ``anomaly.py``, and any digest/trend
consumer share the SAME trend/spike/volume rules instead of each rolling its own.

Rules encoded (all from analysis.ts, verified against the source):
- ``linear_trend`` — OLS slope/intercept + R² (``sxy²/(sxx·syy)``); R²<0.3 = a weak fit to caveat.
- ``trend_direction`` — needs ≥ ``MIN_TREND_POINTS`` (6) points; classifies by window %-of-mean
  change with ±``TREND_BAND_PCT`` (15%) dead-band; below 6 points → "insufficient".
- ``median`` / ``median_abs_deviation`` — outlier-robust dispersion (MAD), so one historical spike
  can't silently raise the bar and mask the next real one (plain stddev can).
- ``is_spike`` — value exceeds ``median + 4×MAD`` (MAD=0 → legacy ``3×median`` fallback) AND a
  ``SPIKE_VALUE_FLOOR`` (10) absolute floor that suppresses tiny-baseline noise.
- ``spike_severity`` — z≥3 or Δ≥100% vs mean → "severe"; z≥2 → "moderate"; else "mild".
- ``meaningful_pct_change`` — a %-change is only reported when the prior base ≥ ``MIN_VOLUME_FLOOR``
  (10); "a jump from 2 to 8 is noise, not an incident."
- ``TOP1_CONCENTRATION_PCT`` (60) — the externally-validated top-1-user concentration bar (tightening
  CRITICAL CROSS-CHECK); exported for consumers to reference, not applied here.
"""
import math

MIN_TREND_POINTS = 6
TREND_BAND_PCT = 15.0        # ±15% window change dead-band before calling a direction
WEAK_FIT_R2 = 0.3
SPIKE_VALUE_FLOOR = 10.0
MIN_VOLUME_FLOOR = 10        # prior-period base below this → %-change is noise, report raw counts
TOP1_CONCENTRATION_PCT = 60.0


def _nums(values):
    return [v for v in (values or [])
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]


def linear_trend(values):
    """OLS fit over (index, value). Returns ``{"slope","intercept","r2"}``. R² distinguishes a real
    trend from noise so callers never assert a direction off a poor fit."""
    ys = _nums(values)
    n = len(ys)
    if n < 2:
        return {"slope": 0.0, "intercept": (ys[0] if ys else 0.0), "r2": 0.0}
    mean_x = (n - 1) / 2
    mean_y = sum(ys) / n
    sxx = sxy = syy = 0.0
    for i, y in enumerate(ys):
        dx = i - mean_x
        dy = y - mean_y
        sxx += dx * dx
        sxy += dx * dy
        syy += dy * dy
    if sxx == 0 or syy == 0:
        return {"slope": 0.0, "intercept": mean_y, "r2": 0.0}
    slope = sxy / sxx
    return {"slope": slope, "intercept": mean_y - slope * mean_x, "r2": (sxy * sxy) / (sxx * syy)}


def trend_direction(values):
    """Classify the series trend with fit-quality gating. Returns
    ``{"direction","windowChangePct","r2","points","weakFit"}`` where direction is
    ``increasing``/``decreasing``/``stable`` (>=6 points) or ``insufficient`` (fewer)."""
    ys = _nums(values)
    n = len(ys)
    if n < MIN_TREND_POINTS:
        return {"direction": "insufficient", "windowChangePct": None, "r2": 0.0, "points": n,
                "weakFit": False}
    fit = linear_trend(ys)
    mean_y = sum(ys) / n
    change = (fit["slope"] * (n - 1) / mean_y) * 100 if mean_y else 0.0
    direction = ("increasing" if change > TREND_BAND_PCT
                 else "decreasing" if change < -TREND_BAND_PCT else "stable")
    return {"direction": direction, "windowChangePct": change, "r2": fit["r2"], "points": n,
            "weakFit": direction != "stable" and fit["r2"] < WEAK_FIT_R2}


def median(values):
    ys = sorted(_nums(values))
    if not ys:
        return 0.0
    m = len(ys)
    mid = m // 2
    return ys[mid] if m % 2 else (ys[mid - 1] + ys[mid]) / 2


def median_abs_deviation(values, med=None):
    ys = _nums(values)
    if not ys:
        return 0.0
    med = median(ys) if med is None else med
    return median([abs(v - med) for v in ys])


def is_spike(value, series):
    """True iff ``value`` exceeds ``median + 4×MAD`` (MAD=0 → ``3×median``) and the absolute floor."""
    ys = _nums(series)
    if not ys or value is None or value <= SPIKE_VALUE_FLOOR:
        return False
    med = median(ys)
    if med <= 0:
        return False
    mad = median_abs_deviation(ys, med)
    threshold = (med + 4 * mad) if mad > 0 else (med * 3)
    return value > threshold


def spike_severity(value, series):
    """Grade a spike: z≥3 or Δ≥100% above the mean → "severe"; z≥2 → "moderate"; else "mild"."""
    ys = _nums(series)
    if not ys or value is None:
        return "mild"
    mean = sum(ys) / len(ys)
    var = sum((v - mean) ** 2 for v in ys) / len(ys)
    sd = math.sqrt(var)
    z = (value - mean) / sd if sd > 0 else 0.0
    delta_pct = ((value - mean) / mean) * 100 if mean > 0 else 0.0
    if z >= 3 or delta_pct >= 100:
        return "severe"
    if z >= 2:
        return "moderate"
    return "mild"


def meaningful_pct_change(prior):
    """True iff a period-over-period %-change is worth reporting — prior base ≥ MIN_VOLUME_FLOOR.
    Below it, report raw counts, never a percentage (suppresses the '2→8 = +300%' noise class)."""
    return isinstance(prior, (int, float)) and not isinstance(prior, bool) and prior >= MIN_VOLUME_FLOOR
