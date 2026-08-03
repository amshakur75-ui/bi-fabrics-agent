"""Statistical anomaly detection on the peak-CU series. Port of ``core/anomaly.js``. Pure."""
import math


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _r1(x):
    return math.floor(x * 10 + 0.5) / 10   # JS Math.round(x*10)/10 (half-up, works for negatives)


def _fmt(x):
    return str(int(x)) if x == int(x) else str(x)


def _stats(ys):
    n = len(ys)
    mean = sum(ys) / n
    variance = sum((b - mean) ** 2 for b in ys) / n
    return mean, math.sqrt(variance)


def detect_anomalies(facts=None, history=None, z=2, min_points=4):
    facts = facts or {}
    history = history or []
    anomalies = []
    series = [v for v in ((h.get("metrics") or {}).get("peakCuPct") for h in history if isinstance(h, dict)) if _is_num(v)]
    current = (facts.get("capacity") or {}).get("peakCuPct")

    if len(series) >= min_points and _is_num(current):
        mean, stddev = _stats(series)
        if stddev > 0 and abs(current - mean) > z * stddev:
            sigma = _r1((current - mean) / stddev)
            direction = "above" if current > mean else "below"
            anomalies.append({
                "metric": "peakCuPct",
                "resource": f"capacity {(facts.get('capacity') or {}).get('capacityId') or ''}".strip(),
                "current": current,
                "mean": _r1(mean),
                "stddev": _r1(stddev),
                "sigma": sigma,
                "direction": direction,
                "message": f"Peak CU {_fmt(current)}% is anomalous vs baseline (mean {int(math.floor(mean + 0.5))}%, {_fmt(abs(sigma))}σ {direction}).",
            })
    return anomalies
