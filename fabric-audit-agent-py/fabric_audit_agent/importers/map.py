"""Column mapper. Faithful port of the Node ``core/importers/map.js``.

Turns an arbitrary CSV table into the agent's facts shape, tolerating the many ways
Fabric Capacity Metrics / VertiPaq exports name their columns. Pure: no I/O. Emits a
coverage report so callers can show which column fed which field.
"""
import math
import re

_TRUTHY = {"true", "yes", "1", "enabled", "on", "y"}


def _norm(s):
    return re.sub(r"[^a-z0-9%]", "", str(s if s is not None else "").lower())


def num(v):
    """Parse the first number out of a messy cell ('1,234 ms', '87%', '4.2 GB'); NaN if none."""
    if v is None:
        return float("nan")
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group(0)) if m else float("nan")


def _num0(v):
    """num(v) coerced to 0 when non-finite — mirrors JS `num(v) || 0` (NaN is falsy in JS)."""
    n = num(v)
    return n if math.isfinite(n) else 0


def _truthy(v):
    return str(v if v is not None else "").strip().lower() in _TRUTHY


def _round3(x):
    return math.floor(x * 1000 + 0.5) / 1000   # JS Math.round (half-up); values >= 0


def _fmt(x):
    return str(int(x)) if x == int(x) else str(x)


_MATCHERS = {
    "sku": lambda h: h == "sku" or "skuname" in h or ("sku" in h and "skip" not in h),
    "capacityName": lambda h: "capacity" in h and ("name" in h or "id" in h or h == "capacity"),
    "tenant": lambda h: "tenant" in h,
    "memoryGB": lambda h: "capacitymemory" in h or h in ("memory", "memorygb", "ram", "ramgb"),
    "throttle": lambda h: "throttl" in h or "overload" in h or "interactivedelay" in h,
    "time": lambda h: "timepoint" in h or "timestamp" in h or "datetime" in h or h in ("time", "date"),
    "workspace": lambda h: "workspace" in h,
    "itemName": lambda h: "itemname" in h or h == "item" or "datasetname" in h or h == "dataset" or "semanticmodel" in h or ("model" in h and "name" in h) or h == "name" or "reportname" in h,
    "sizeGB": lambda h: "sizegb" in h or "modelsize" in h or "datasetsize" in h or h == "size" or "dynamicmemory" in h or "totalsize" in h,
    "durationMin": lambda h: "duration" in h,
    "scheduledAt": lambda h: "scheduled" in h or "starttime" in h or h == "start",
    "bidi": lambda h: "bidirection" in h or "bidi" in h or "bothdirection" in h,
    "autoDate": lambda h: "autodate" in h or "autodatetime" in h,
    "failRate": lambda h: "failrate" in h or ("refresh" in h and "fail" in h) or "failurerate" in h or "errorrate" in h,
    "visuals": lambda h: "visual" in h and "ms" not in h and "slow" not in h,
    "mode": lambda h: h == "mode" or "storagemode" in h or "connectionmode" in h,
    "slowest": lambda h: "slowest" in h or "renderms" in h or "rendertime" in h or ("visual" in h and "ms" in h),
}


def _find(headers, key):
    for h in headers:
        if _MATCHERS[key](_norm(h)):
            return h
    return None


def find_cu_pct(headers):
    """Find the capacity utilization % column, preferring true overall-usage over
    look-alikes ('100% in CU(s)' baseline, 'CU % Limit', 'Background %/Interactive %')."""
    tagged = [(h, _norm(h)) for h in headers]
    tiers = [
        lambda t: "totalcuusage" in t[1] or ("usage" in t[1] and "cu" in t[1] and "%" in t[1]) or "utiliz" in t[1],
        lambda t: "%ofbase" in t[1] or "ofbasecapacity" in t[1],
        lambda t: "cu" in t[1] and ("%" in t[1] or "pct" in t[1] or "percent" in t[1])
        and "limit" not in t[1] and "100%in" not in t[1] and "nonbillable" not in t[1]
        and not t[1].startswith("background") and not t[1].startswith("interactive") and "autoscale" not in t[1],
    ]
    for pred in tiers:
        for t in tagged:
            if pred(t):
                return t[0]
    return None


def _norm_mode(v):
    h = _norm(v)
    if "direct" in h:
        return "DirectQuery"
    if "import" in h:
        return "Import"
    if "dual" in h:
        return "Dual"
    if "live" in h:
        return "LiveConnection"
    return str(v if v is not None else "").strip()


def _cell(r, col):
    return str(r.get(col) or "").strip() if col else ""


def map_table(headers, rows=None):
    rows = rows or []
    cov = []
    cols = {key: _find(headers, key) for key in _MATCHERS}
    cols["cuPct"] = find_cu_pct(headers)

    def first_non_empty(col):
        if not col:
            return ""
        for r in rows:
            v = str(r.get(col) or "").strip()
            if v != "":
                return v
        return ""

    def note(looked):
        return f"no column found (looked for: {looked})"

    capacity = None
    has_capacity_signal = cols["cuPct"] or cols["throttle"] or cols["sku"] or cols["capacityName"] or cols["memoryGB"]
    if has_capacity_signal:
        peak_cu_pct = 0
        peak_at = ""
        if cols["cuPct"]:
            for r in rows:
                v = num(r.get(cols["cuPct"]))
                if math.isfinite(v) and v > peak_cu_pct:
                    peak_cu_pct = v
                    peak_at = _cell(r, cols["time"]) if cols["time"] else ""
            entry = {"field": "peakCuPct", "source": cols["cuPct"], "value": f"{_fmt(peak_cu_pct)}%"}
            if peak_cu_pct > 1000:
                entry["note"] = "that looks like CU-seconds, not a %, double-check the column"
            cov.append(entry)
        else:
            cov.append({"field": "peakCuPct", "source": None, "value": 0, "note": note("Total CU Usage %, utilization, % of base")})

        throttle_minutes = 0
        if cols["throttle"]:
            for r in rows:
                v = num(r.get(cols["throttle"]))
                if math.isfinite(v):
                    throttle_minutes += v
            cov.append({"field": "throttleMinutes", "source": cols["throttle"], "value": throttle_minutes})
        else:
            cov.append({"field": "throttleMinutes", "source": None, "value": 0, "note": note("throttling, overloaded, rejected")})

        sku = first_non_empty(cols["sku"])
        capacity_id = first_non_empty(cols["capacityName"]) or sku or "unnamed"
        memory_gb = num(first_non_empty(cols["memoryGB"]))
        cov.append({"field": "sku", "source": cols["sku"], "value": sku or "(none)"})
        cov.append({"field": "capacityId", "source": cols["capacityName"], "value": capacity_id})

        capacity = {
            "tenant": first_non_empty(cols["tenant"]) or "tenant",
            "capacityId": capacity_id,
            "sku": sku or "",
            "memoryGB": memory_gb if math.isfinite(memory_gb) else 0,
            "peakCuPct": peak_cu_pct,
            "peakAt": peak_at,
            "throttleMinutes": throttle_minutes,
            "refreshes": [],
        }

        if cols["itemName"] and (cols["sizeGB"] or cols["durationMin"] or cols["scheduledAt"]):
            for r in rows:
                dataset = _cell(r, cols["itemName"])
                if not dataset:
                    continue
                if cols["scheduledAt"]:
                    sched = _cell(r, cols["scheduledAt"])
                elif cols["time"]:
                    sched = _cell(r, cols["time"])
                else:
                    sched = ""
                capacity["refreshes"].append({
                    "workspace": _cell(r, cols["workspace"]),
                    "dataset": dataset,
                    "scheduledAt": sched,
                    "durationMin": _num0(r.get(cols["durationMin"])) if cols["durationMin"] else 0,
                    "sizeGB": _num0(r.get(cols["sizeGB"])) if cols["sizeGB"] else 0,
                })
            cov.append({"field": "capacity.refreshes", "source": cols["itemName"], "value": f"{len(capacity['refreshes'])} row(s)"})

    models = []
    if cols["itemName"] and (cols["bidi"] or cols["autoDate"] or cols["failRate"]):
        for r in rows:
            name = _cell(r, cols["itemName"])
            if not name:
                continue
            models.append({
                "workspace": _cell(r, cols["workspace"]),
                "name": name,
                "sizeGB": _num0(r.get(cols["sizeGB"])) if cols["sizeGB"] else 0,
                "bidirectionalRels": _num0(r.get(cols["bidi"])) if cols["bidi"] else 0,
                "autoDateTime": _truthy(r.get(cols["autoDate"])) if cols["autoDate"] else False,
                "refreshFailRatePct": _num0(r.get(cols["failRate"])) if cols["failRate"] else 0,
            })
        cov.append({"field": "models", "source": cols["itemName"], "value": f"{len(models)} row(s)"})

    reports = []
    if cols["itemName"] and (cols["visuals"] or cols["mode"] or cols["slowest"]):
        for r in rows:
            name = _cell(r, cols["itemName"])
            if not name:
                continue
            reports.append({
                "workspace": _cell(r, cols["workspace"]),
                "name": name,
                "visuals": _num0(r.get(cols["visuals"])) if cols["visuals"] else 0,
                "mode": _norm_mode(r.get(cols["mode"])) if cols["mode"] else "Import",
                "slowestVisualMs": _num0(r.get(cols["slowest"])) if cols["slowest"] else 0,
            })
        cov.append({"field": "reports", "source": cols["itemName"], "value": f"{len(reports)} row(s)"})

    return {"capacity": capacity, "models": models, "reports": reports, "coverage": cov}


def merge_facts(parts):
    facts = {}
    caps = [p.get("capacity") for p in parts if p and p.get("capacity")]
    if caps:
        capacity = {"tenant": "tenant", "capacityId": "", "sku": "", "memoryGB": 0, "peakCuPct": 0, "peakAt": "", "throttleMinutes": 0, "refreshes": []}
        for c in caps:
            if c.get("tenant") and capacity["tenant"] == "tenant":
                capacity["tenant"] = c["tenant"]
            capacity["capacityId"] = capacity["capacityId"] or c.get("capacityId")
            capacity["sku"] = capacity["sku"] or c.get("sku")
            capacity["memoryGB"] = capacity["memoryGB"] or c.get("memoryGB")
            if (c.get("peakCuPct") or 0) > capacity["peakCuPct"]:
                capacity["peakCuPct"] = c.get("peakCuPct")
                capacity["peakAt"] = c.get("peakAt") or capacity["peakAt"]
            capacity["throttleMinutes"] += c.get("throttleMinutes") or 0
            capacity["refreshes"].extend(c.get("refreshes") or [])
        capacity["capacityId"] = capacity["capacityId"] or "unnamed"
        capacity["peakCuPct"] = _round3(capacity["peakCuPct"])
        facts["capacity"] = capacity
    models = [m for p in parts for m in (p.get("models") or [])]
    if models:
        facts["models"] = models
    reports = [r for p in parts for r in (p.get("reports") or [])]
    if reports:
        facts["reports"] = reports
    return facts
