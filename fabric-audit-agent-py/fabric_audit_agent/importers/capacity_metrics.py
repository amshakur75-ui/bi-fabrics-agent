"""Schema-aware readers for the real Fabric Capacity Metrics CSV exports.

Faithful port of the Node ``core/importers/capacity-metrics.js``. The generic mapper
(map.py) is too loose for these specific tables, so we recognize them explicitly. Pure.
"""
import math
import re
from .map import num

_LABELISH = ("name", "workspace", "dataset")


def _norm(s):
    return re.sub(r"[^a-z0-9%]", "", str(s if s is not None else "").lower())


def _find_h(headers, pred):
    for h in headers:
        if pred(_norm(h)):
            return h
    return None


def _round1(x):
    return math.floor(x * 10 + 0.5) / 10


def _num0(v):
    n = num(v)
    return n if math.isfinite(n) else 0


def _cell(r, col):
    return str(r.get(col) or "").strip() if col else ""


def _is_name(n):
    return "itemname" in n or n == "item" or ("item" in n and "name" in n) or "datasetname" in n


def _is_cu(n):
    return (n == "cus" or "cus" in n or ("cu" in n and "second" in n)) and "%" not in n


# ───────────────────────── Items table ─────────────────────────
def looks_like_items(headers):
    norms = [_norm(h) for h in headers]
    return any(_is_name(n) for n in norms) and any(_is_cu(n) for n in norms)


def map_items(headers, rows):
    ws = _find_h(headers, lambda n: "workspace" in n)
    kind = _find_h(headers, lambda n: "itemkind" in n or n == "kind" or "itemtype" in n)
    name = _find_h(headers, _is_name)
    cu = _find_h(headers, _is_cu)
    dur = _find_h(headers, lambda n: "duration" in n)
    users = _find_h(headers, lambda n: n == "users" or "usercount" in n)
    rej = _find_h(headers, lambda n: "reject" in n)

    items = []
    for r in rows:
        it = {
            "workspace": _cell(r, ws),
            "kind": _cell(r, kind),
            "name": _cell(r, name),
            "cuSeconds": _num0(r.get(cu)) if cu else 0,
            "durationSec": _num0(r.get(dur)) if dur else 0,
            "users": _num0(r.get(users)) if users else 0,
            "rejected": _num0(r.get(rej)) if rej else 0,
        }
        if it["name"]:
            items.append(it)

    total_cu = sum(it["cuSeconds"] for it in items)
    rejected_total = sum(it["rejected"] for it in items)
    for it in items:
        it["sharePct"] = _round1(it["cuSeconds"] / total_cu * 100) if total_cu else 0
    top = [dict(it, pctOfTotal=it["sharePct"]) for it in sorted(items, key=lambda it: -it["cuSeconds"])[:10]]
    rejected_items = sorted([it for it in items if it["rejected"] > 0], key=lambda it: -it["rejected"])

    return {
        "items": items, "itemCount": len(items), "totalCu": total_cu, "rejectedTotal": rejected_total,
        "top": top, "rejectedItems": rejected_items,
        "columns": {"ws": ws, "kind": kind, "name": name, "cu": cu, "dur": dur, "users": users, "rej": rej},
    }


# ─────────────────────── Timepoint table ───────────────────────
def looks_like_timepoints(headers):
    norms = [_norm(h) for h in headers]
    has_time = any("timepoint" in n or n == "time" or n == "datetime" for n in norms)
    has_cu = any("totalcu" in n or "100%in" in n or "cuusage" in n for n in norms)
    return has_time and has_cu


def analyze_timepoints(headers, rows):
    usage_pct = _find_h(headers, lambda n: "totalcuusage" in n or ("usage" in n and "%" in n) or "utiliz" in n)
    total_cu = _find_h(headers, lambda n: "totalcus" in n or ("total" in n and "cus" in n))
    base_hdr = _find_h(headers, lambda n: "100%in" in n)
    state_hdr = _find_h(headers, lambda n: "state" in n)
    time = _find_h(headers, lambda n: "timepoint" in n or n == "time")

    reported_peak, reported_at = None, ""
    if usage_pct:
        mx = float("-inf")
        for r in rows:
            v = num(r.get(usage_pct))
            if math.isfinite(v) and v > mx:
                mx = v
                reported_at = _cell(r, time) if time else ""
        reported_peak = None if mx == float("-inf") else _round1(mx)

    baseline = float("nan")
    if base_hdr:
        for r in rows:
            v = num(r.get(base_hdr))
            if math.isfinite(v) and v > 0:
                baseline = v
                break

    computed_peak, computed_at = None, ""
    if total_cu and math.isfinite(baseline) and baseline > 0:
        mx = float("-inf")
        for r in rows:
            v = num(r.get(total_cu))
            if math.isfinite(v):
                p = v / baseline * 100
                if p > mx:
                    mx = p
                    computed_at = _cell(r, time) if time else ""
        computed_peak = None if mx == float("-inf") else _round1(mx)

    states = {}
    if state_hdr:
        for r in rows:
            v = _cell(r, state_hdr) or "(blank)"
            states[v] = states.get(v, 0) + 1

    return {
        "reportedPeakPct": reported_peak, "reportedAt": reported_at,
        "computedPeakPct": computed_peak, "computedAt": computed_at,
        "baseline": baseline if math.isfinite(baseline) else None, "states": states,
        "columns": {"usagePct": usage_pct, "totalCu": total_cu, "baseHdr": base_hdr, "stateHdr": state_hdr},
    }


# ──────────────────── safe column inspector ────────────────────
def inspect_columns(headers, rows):
    out = []
    for h in headers:
        n = _norm(h)
        vals = [v for v in (str(r.get(h) or "").strip() for r in rows) if v != ""]
        looks_time = "timepoint" in n or n == "time" or "datetime" in n
        nums = [x for x in (num(v) for v in vals) if math.isfinite(x)]
        if not looks_time and len(vals) and len(nums) >= len(vals) * 0.6:
            s = sorted(nums)
            out.append({"column": h, "type": "number", "count": len(nums), "min": s[0], "max": s[-1],
                        "median": s[len(s) // 2], "sum": math.floor(sum(nums) * 100 + 0.5) / 100})
            continue
        distinct = len(set(vals))
        if looks_time:
            out.append({"column": h, "type": "time", "count": len(vals), "distinct": distinct})
        elif any(tok in n for tok in _LABELISH):
            out.append({"column": h, "type": "label", "count": len(vals), "distinct": distinct})
        elif distinct <= 15:
            out.append({"column": h, "type": "category", "distinct": distinct, "values": sorted(set(vals))})
        else:
            out.append({"column": h, "type": "text", "distinct": distinct})
    return out


# ──────────── capacity facts from a timepoints table (robust to raw-% spikes) ────────────
from datetime import datetime  # noqa: E402 - local to this helper


def _parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001 - any unparseable timestamp is simply skipped
        return None


def _percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, int(round(p / 100 * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def capacity_signal_from_timepoints(headers, rows):
    """Trustworthy capacity signal from a Capacity Metrics *timepoints* table.

    The raw "Total CU Usage %" column holds pre-smoothing spikes (e.g. 23000%+), so instead we
    compute utilization per row as ``Total CU(s) / 100% in CU(s) * 100`` (using each row's own
    baseline — it can change mid-window on a resize) and take the **p95** as a spike-robust peak.
    Throttle is read from the **Capacity State** column's ``Overloaded`` windows, converted to
    minutes via the median timepoint interval. Returns ``{peakCuPct, throttleMinutes, overloadedCount}``.
    """
    total_cu = _find_h(headers, lambda n: "totalcus" in n or ("total" in n and "cus" in n))
    base_hdr = _find_h(headers, lambda n: "100%in" in n)
    state_hdr = _find_h(headers, lambda n: "state" in n)
    time = _find_h(headers, lambda n: "timepoint" in n or n == "time")

    utils, times, overloaded = [], [], 0
    for r in rows:
        if total_cu and base_hdr:
            cu, base = num(r.get(total_cu)), num(r.get(base_hdr))
            if math.isfinite(cu) and math.isfinite(base) and base > 0:
                utils.append(cu / base * 100)
        if state_hdr and _cell(r, state_hdr).lower() == "overloaded":
            overloaded += 1
        if time:
            t = _parse_ts(_cell(r, time))
            if t:
                times.append(t)

    utils.sort()
    p95 = _percentile(utils, 95)

    window_min = None
    if len(times) >= 2:
        ts = sorted(times)
        diffs = sorted((ts[i + 1] - ts[i]).total_seconds() for i in range(len(ts) - 1))
        med = diffs[len(diffs) // 2]
        if med > 0:
            window_min = med / 60
    throttle_min = math.floor(overloaded * window_min + 0.5) if (overloaded and window_min) else overloaded

    return {
        "peakCuPct": _round1(p95) if p95 is not None else 0,
        "throttleMinutes": throttle_min,
        "overloadedCount": overloaded,
    }
