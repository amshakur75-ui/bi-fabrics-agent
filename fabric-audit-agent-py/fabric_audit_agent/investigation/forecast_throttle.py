"""Time-to-throttle forecast: robust-trend (Theil-Sen-style) projection of the CU% series to a
threshold (default 100%).

Spec decision (ADD 3, deliberately simple): a foundation model (TimesFM) was evaluated for this
forecast and REJECTED. The series is a single clean metric (CU%) with a hard, known threshold and
no exotic seasonality a foundation model would need to learn -- a robust median-of-pairwise-slopes
trend captures the signal a foundation model would, without the extra dependency, latency, or
non-determinism. This module is pure / stdlib-only (statistics.median) and deterministic -- no
randomness, no datetime.now(), no ML.

Complements (never re-derives) Task 4's `minutesToBurndown` passthrough in
investigation/throttle.py, which is the Capacity Metrics app's OWN figure, verbatim. This forecast
is our own independently-computed projection, named `timeToThreshold`/`timeToThrottle` on purpose
to avoid any confusion with that passthrough or with the unrelated run-history `forecast`
(automation/pipeline `forecast_capacity` -- daily peaks, different granularity).

Timestamp handling: reuses investigation.patterns._parse_minutes, which returns
`day_ordinal*1440 + hour*60 + minute` -- a monotone cross-day PROXY (not epoch minutes) that drops
seconds. That's fine here because the slope only ever uses relative deltas between points, but two
points that land in the same minute collapse to Delta-t == 0 -- those pairs are filtered out before
taking the median of pairwise slopes.
"""
import math
from statistics import median

from .patterns import _parse_minutes

_WINDOW = 48  # Theil-Sen-style: median of pairwise slopes over at most the last N points.


def _num(v):
    # mirrors JS Number.isFinite: rejects bool, NaN, and Infinity (see investigation/throttle.py)
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def forecast_time_to_threshold(series, *, threshold=100.0, min_points=8):
    """Project when the CU% series crosses `threshold`, via a Theil-Sen-style robust slope
    (median of pairwise slopes, filtering zero-Delta-t pairs) over the last min(len, 48) points.

    Returns {"minutesToThreshold": float|None, "method": "robust-trend",
             "slopePctPerMin": float|None, "basis": str}.
    """
    pts = [(p.get("ts"), p.get("cuPct")) for p in (series or []) if _num(p.get("cuPct"))]
    pts = [(ts, cu) for ts, cu in pts if _parse_minutes(ts) is not None]

    if pts and pts[-1][1] >= threshold:
        return {"minutesToThreshold": 0.0, "method": "robust-trend",
                "slopePctPerMin": None, "basis": "already at or above threshold"}

    if len(pts) < min_points:
        return {"minutesToThreshold": None, "method": "robust-trend",
                "slopePctPerMin": None,
                "basis": f"fewer than {min_points} points"}

    window = pts[-_WINDOW:]
    last_cu = window[-1][1]
    mins = [_parse_minutes(ts) for ts, _ in window]
    cus = [cu for _, cu in window]
    n = len(window)

    slopes = []
    for i in range(n):
        for j in range(i + 1, n):
            dt = mins[j] - mins[i]
            if dt == 0:
                continue
            slopes.append((cus[j] - cus[i]) / dt)

    if not slopes:
        return {"minutesToThreshold": None, "method": "robust-trend",
                "slopePctPerMin": None,
                "basis": "not rising -- no usable point-pairs (all same-minute timestamps)"}

    slope = median(slopes)
    if slope <= 0:
        return {"minutesToThreshold": None, "method": "robust-trend",
                "slopePctPerMin": slope, "basis": "not rising"}

    minutes = (threshold - last_cu) / slope
    return {"minutesToThreshold": minutes, "method": "robust-trend",
            "slopePctPerMin": slope, "basis": "robust-trend projection"}
