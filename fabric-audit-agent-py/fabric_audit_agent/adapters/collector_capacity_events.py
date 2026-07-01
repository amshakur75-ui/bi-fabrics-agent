"""Capacity-events CollectorPort — live capacity CU% / throttle from Real-Time Hub Capacity Overview Events.

Reads the ``Microsoft.Fabric.Capacity.Summary`` events (30-second windows) that an Eventstream lands in
a CUSTOM Eventhouse — NOT the Workspace Monitoring Eventhouse, so this coexists with a workspace's Azure
Log Analytics export (the two live on separate planes; the workspace monitoring-vs-LA either/or does not
apply here). This fills the authoritative capacity CU% the REST APIs don't expose, so the verdict
(optimize vs size-up) finally has a real ``peakCuPct`` instead of null.

CU% per window = capacityUnitMs / (baseCapacityUnits * 1000 * 30) * 100   (official KQL).

Operational caveats baked in (from the docs):
  - **Best-effort delivery** can duplicate events → we DEDUPE to one row per (capacityId, window).
  - **No historical backfill** → peak/throttle are only over what the Eventhouse has collected; start
    streaming early. The ``window`` just bounds the query, it can't recover pre-collection history.
  - **P-SKU + autoscale**: utilization % isn't computable (budget excludes autoscale units) → rows with
    no positive budget are skipped. F-SKU is unaffected.

``query`` is injected (``query(kql) -> list[dict]``); swaps to ``adapters.clients.build_kusto_query`` at
deploy (same Kusto/KQL API as Workspace Monitoring). The default KQL windows by ``ingestion_time()`` (a
Kusto built-in, schema-independent); set ``FABRIC_CAPACITY_EVENTS_KQL`` if your landed column names differ.
"""

_WINDOW_SEC = 30


def _row(r, *names):
    # Capacity Overview Events arrive with fields nested under a ``data`` envelope
    # (data.capacityUnitMs, data.baseCapacityUnits, ...); resolve top-level first, then inside ``data``.
    data = r.get("data") if isinstance(r.get("data"), dict) else {}
    for n in names:
        if r.get(n) is not None:
            return r[n]
        if data.get(n) is not None:
            return data[n]
    return None


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _default_kql(table, window):
    # ingestion_time() is always available regardless of how the Eventstream mapped the JSON columns,
    # so the default query never errors on a schema mismatch. Dedupe + math happen in Python below.
    return f"['{table}']\n| where ingestion_time() > ago({window})"


def create_capacity_events_collector(query, config=None):
    """``config`` keys: ``table`` (Eventhouse table the eventstream writes to, default "CapacityEvents"),
    ``window`` (lookback, default "1d"), ``kql`` (override the whole query)."""
    cfg = config or {}
    table = cfg.get("table", "CapacityEvents")
    kql = cfg.get("kql") or _default_kql(table, cfg.get("window", "1d"))

    def collect():
        rows = query(kql) or []

        # Dedupe to one row per (capacityId, window) — best-effort delivery can duplicate.
        seen = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            cap = str(_row(r, "capacityId", "CapacityId", "capacityid") or "")
            win = str(_row(r, "windowStartTime", "WindowStartTime", "windowStart", "startTime", "timestamp") or "")
            seen[(cap, win)] = r

        peak = None
        peak_at = ""
        over_windows = 0
        usable = 0
        cap_id = ""
        for r in seen.values():
            base = _num(_row(r, "baseCapacityUnits", "BaseCapacityUnits"))
            used = _num(_row(r, "capacityUnitMs", "CapacityUnitMs"))
            if base is None or used is None or base <= 0:
                continue   # P-SKU autoscale / missing fields → can't compute %, skip
            budget = base * 1000 * _WINDOW_SEC
            if budget <= 0:
                continue
            usable += 1
            pct = used / budget * 100
            cap_id = cap_id or str(_row(r, "capacityId", "CapacityId") or "")
            if peak is None or pct > peak:
                peak = pct
                peak_at = str(_row(r, "windowStartTime", "WindowStartTime", "startTime", "timestamp") or "")
            if pct >= 100:
                over_windows += 1

        if not usable:
            return {}   # nothing computable → contribute nothing; merge keeps other sources

        cap = {
            "peakCuPct": round(peak, 1),
            "peakAt": peak_at,
            "throttleMinutes": round(over_windows * _WINDOW_SEC / 60, 1),
        }
        if cap_id:
            cap["capacityId"] = cap_id
        return {"capacity": cap}

    return {"collect": collect}


def capacity_series(query, config=None):
    """Return per-window ``[{ts, cuPct}]`` sorted by ``ts`` — the full series, NOT reduced to the
    peak (``create_capacity_events_collector`` above does the reduction; ``capacity_patterns``
    needs the series to correlate CU% against event-activity buckets). Same dedupe + CU% math as
    the peak collector; read-only."""
    cfg = config or {}
    table = cfg.get("table", "CapacityEvents")
    kql = cfg.get("kql") or _default_kql(table, cfg.get("window", "1d"))
    rows = query(kql) or []

    seen = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        cap = str(_row(r, "capacityId", "CapacityId", "capacityid") or "")
        win = str(_row(r, "windowStartTime", "WindowStartTime", "windowStart", "startTime", "timestamp") or "")
        seen[(cap, win)] = r

    out = []
    for (cap, win), r in seen.items():
        base = _num(_row(r, "baseCapacityUnits", "BaseCapacityUnits"))
        used = _num(_row(r, "capacityUnitMs", "CapacityUnitMs"))
        if base is None or used is None or base <= 0:
            continue
        budget = base * 1000 * _WINDOW_SEC
        if budget <= 0:
            continue
        out.append({"ts": win, "cuPct": round(used / budget * 100, 1)})

    return sorted(out, key=lambda p: p["ts"])
