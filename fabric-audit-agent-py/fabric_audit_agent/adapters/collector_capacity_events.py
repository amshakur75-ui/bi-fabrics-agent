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
from ..query.kql_guard import escape_entity, first_statement

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
    return f"{escape_entity(table)}\n| where ingestion_time() > ago({window})"


def _resolve_kql(cfg):
    """Resolve the query: a trusted ``kql`` override when present (with ``{window}`` substituted
    from the config window so a threaded lookback isn't silently defeated by a hardcoded
    ``ago(...)``; overrides without the placeholder behave exactly as before) is passed through
    UNMODIFIED otherwise -- first_statement() would wrongly truncate a multi-line/`let` flatten. The
    schema-independent BUILT default is guarded with first_statement() as defense-in-depth against
    an unescaped/unquoted interpolation seam (e.g. ``window``)."""
    window = cfg.get("window", "1d")
    override = cfg.get("kql")
    if override:
        return override.replace("{window}", window)
    return first_statement(_default_kql(cfg.get("table", "CapacityEvents"), window))


def _windows(rows):
    """Dedupe Capacity Overview Events to one row per (capacityId, window) and compute CU% per
    window. Returns ``[{"cap", "ts", "pct"}]`` for every window with a positive budget; P-SKU
    autoscale / missing-field rows (no positive budget) are skipped -- they can't yield a %.

    Shared by ``create_capacity_events_collector`` (which reduces to the peak) and
    ``capacity_series`` (which keeps every point), so the dedupe + the official
    ``capacityUnitMs / (baseCapacityUnits*1000*30) * 100`` math live in exactly ONE place. The
    window ``ts`` and ``cap`` are resolved from the SAME field lists used to build the dedupe key,
    so a downstream ``peakAt`` / series ``ts`` can never disagree with the key -- e.g. a row
    carrying only ``windowStart`` still surfaces its timestamp."""
    # Best-effort delivery can duplicate → dedupe to one row per (capacityId, window).
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
            continue   # P-SKU autoscale / missing fields → can't compute %, skip
        budget = base * 1000 * _WINDOW_SEC
        if budget <= 0:
            continue
        out.append({"cap": cap, "ts": win, "pct": used / budget * 100})
    return out


def create_capacity_events_collector(query, config=None):
    """``config`` keys: ``table`` (Eventhouse table the eventstream writes to, default "CapacityEvents"),
    ``window`` (lookback, default "1d"), ``kql`` (override the whole query; a ``{window}``
    placeholder in it is substituted with the config window)."""
    cfg = config or {}
    kql = _resolve_kql(cfg)

    def collect():
        windows = _windows(query(kql) or [])
        if not windows:
            return {}   # nothing computable → contribute nothing; merge keeps other sources

        peak_w = max(windows, key=lambda w: w["pct"])   # first max wins on ties (insertion order)
        over_windows = sum(1 for w in windows if w["pct"] >= 100)
        cap_id = next((w["cap"] for w in windows if w["cap"]), "")

        cap = {
            "peakCuPct": round(peak_w["pct"], 1),
            "peakAt": peak_w["ts"],
            "throttleMinutes": round(over_windows * _WINDOW_SEC / 60, 1),
        }
        if cap_id:
            cap["capacityId"] = cap_id
        return {"capacity": cap}

    return {"collect": collect}


def capacity_base_cu(query, config=None):
    """Return the LIVE base capacity units (e.g. 1024 for F1024) read fresh from the capacity-events
    stream's ``baseCapacityUnits`` -- the authoritative base AT QUERY TIME. This is correct even when
    the registered SKU *name* is a trial/non-standard string (e.g. "FTL64") or the capacity was
    resized/autoscaled, so % of base never rests on a stale name. Returns the MAX positive base seen
    in the window (the real prod capacity dominates a small trial capacity if both appear); None when
    no positive base is present. Read-only; shares ``_resolve_kql`` with the other collectors."""
    rows = query(_resolve_kql(config or {})) or []
    bases = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        base = _num(_row(r, "baseCapacityUnits", "BaseCapacityUnits"))
        if base is not None and base > 0:
            bases.append(base)
    return max(bases) if bases else None


def capacity_series(query, config=None):
    """Return per-window ``[{ts, cuPct}]`` sorted by ``ts`` — the full series, NOT reduced to the
    peak (``create_capacity_events_collector`` above does the reduction; ``capacity_patterns``
    needs the series to correlate CU% against event-activity buckets). Shares ``_windows`` (dedupe +
    CU% math) and ``_resolve_kql`` ({window} substitution) with the peak collector; read-only."""
    cfg = config or {}
    windows = _windows(query(_resolve_kql(cfg)) or [])
    return sorted(
        [{"ts": w["ts"], "cuPct": round(w["pct"], 1)} for w in windows],
        key=lambda p: p["ts"],
    )
