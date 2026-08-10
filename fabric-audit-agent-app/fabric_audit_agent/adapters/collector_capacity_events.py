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
        point = {"cap": cap, "ts": win, "pct": used / budget * 100}

        # A1 — Throttle threshold signals: raw API = 0–1 fraction → scale ×100 so
        # throttle.py's `max(vals) > 100.0` check fires correctly.  Without this
        # scaling a raw fraction (e.g. 1.237) would need to exceed 100 to trip the
        # gate — permanently silent regardless of actual throttle state.
        for camel, pascal, dst in (
            ("interactiveDelayThresholdPercentage",
             "InteractiveDelayThresholdPercentage", "interactiveDelayPct"),
            ("interactiveRejectionThresholdPercentage",
             "InteractiveRejectionThresholdPercentage", "interactiveRejectionPct"),
            ("backgroundRejectionThresholdPercentage",
             "BackgroundRejectionThresholdPercentage", "backgroundRejectionPct"),
        ):
            v = _num(_row(r, camel, pascal))
            if v is not None:
                point[dst] = v * 100

        # A2 — Overage / carry-forward chain fields (formula proven against 1,777
        # consecutive 30-second windows, GAPS-AND-ISSUES Section 12.3;
        # Burndown is stored as negative by the API).
        for camel, pascal, dst in (
            ("overageAddCapacityUnitMs",      "OverageAddCapacityUnitMs",      "overageAddMs"),
            ("overageBurndownCapacityUnitMs", "OverageBurndownCapacityUnitMs", "overageBurndownMs"),
            ("overageTotalCapacityUnitMs",    "OverageTotalCapacityUnitMs",    "overageTotalMs"),
        ):
            v = _num(_row(r, camel, pascal))
            if v is not None:
                point[dst] = v

        # Derive minutesToBurndown when overageTotalMs is present:
        #   cumulativePct  = overageTotal / (base × 1000 × 30) × 100
        #   minutesToBurndown = cumulativePct / 200  (constant divisor, verified exact)
        overage_total = point.get("overageTotalMs")
        if overage_total is not None:
            cumulative_pct = overage_total / budget * 100
            point["overageCumulativePct"] = round(cumulative_pct, 2)
            point["minutesToBurndown"] = round(cumulative_pct / 200, 2)

        out.append(point)
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
        # Design A': carry the MAX of each Fabric throttle-threshold pct across windows so the
        # throttle-imminent detector (tier2_check._check_throttle_imminent) can fire on
        # "approaching throttle" without waiting for the actual throttle signal. These are
        # already scaled ×100 by ``_windows`` (0-100+ range). Omitted when the source has no
        # threshold data (rare — but the check just doesn't fire, no fallback fabrication).
        for f in ("interactiveDelayPct", "interactiveRejectionPct", "backgroundRejectionPct"):
            vals = [w[f] for w in windows if w.get(f) is not None]
            if vals:
                cap["max" + f[0].upper() + f[1:]] = round(max(vals), 2)
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
    """Return per-window ``[{ts, cuPct, ...}]`` sorted by ``ts`` — the full series, NOT reduced to
    the peak (``create_capacity_events_collector`` above does the reduction; ``capacity_patterns``
    needs the series to correlate CU% against event-activity buckets). Shares ``_windows`` (dedupe +
    CU% math + field extraction) and ``_resolve_kql`` ({window} substitution) with the peak
    collector; read-only.

    A1 — Now includes throttle threshold signal fields (``interactiveDelayPct``,
    ``interactiveRejectionPct``, ``backgroundRejectionPct``) when present, so
    ``throttle.py``’s stage-2 gate can actually fire.

    A2 — Now includes overage / burndown chain fields (``overageAddMs``,
    ``overageBurndownMs``, ``overageTotalMs``, ``overageCumulativePct``,
    ``minutesToBurndown``) when present."""
    cfg = config or {}
    windows = _windows(query(_resolve_kql(cfg)) or [])

    def _series_point(w):
        pt = {"ts": w["ts"], "cuPct": round(w["pct"], 1)}
        # A1 — throttle threshold signal fields (scaled ×100 by _windows)
        for f in ("interactiveDelayPct", "interactiveRejectionPct", "backgroundRejectionPct"):
            if w.get(f) is not None:
                pt[f] = round(w[f], 2)
        # A2 — overage / burndown chain fields
        for f in ("overageAddMs", "overageBurndownMs", "overageTotalMs",
                  "overageCumulativePct", "minutesToBurndown"):
            if w.get(f) is not None:
                pt[f] = w[f]
        return pt

    return sorted([_series_point(w) for w in windows], key=lambda p: p["ts"])


def burndown_chain_from_series(series):
    """Pure core of the burndown chain: filter an ALREADY-FETCHED series (as produced by
    ``capacity_series()``) down to windows carrying overage data, in ``ts`` order. Shared by
    ``capacity_burndown_chain()`` below (which fetches its own series via a live query) and
    ``diagnose.py`` (which already has a series in hand and must not re-query for it)."""
    chain = []
    for w in sorted(series or [], key=lambda p: p.get("ts", "")):
        if w.get("overageTotalMs") is not None:
            chain.append({
                "ts": w["ts"],
                "cuPct": w.get("cuPct", w.get("pct")),
                "overageAddMs": w.get("overageAddMs"),
                "overageBurndownMs": w.get("overageBurndownMs"),
                "overageTotalMs": w["overageTotalMs"],
                "overageCumulativePct": w.get("overageCumulativePct"),
                "minutesToBurndown": w.get("minutesToBurndown"),
            })
    return chain


def capacity_burndown_chain(query, config=None):
    """Return the per-window carry-forward / burndown chain for windows where overage data
    is present.  Only windows with ``overageTotalMs`` are included; use ``capacity_series``
    when you need the full unfiltered series.  Read-only; shares ``_windows`` +
    ``_resolve_kql`` with the other collectors.

    Formula (proven against 1,777 consecutive 30-second windows, 2026-07-27 validation
    session, GAPS-AND-ISSUES Section 12.3)::

        Cumulative[T] = Cumulative[T-1] + Add[T-1] + Burndown[T-1]
        (Burndown is stored negative by the API; recursion is one-window lagged)
        minutesToBurndown = overageCumulativePct / 200  (constant divisor, verified exact)

    Called from ``diagnose.py`` when ``timepointsOver > 0`` (A2) -- via ``burndown_chain_from_series``
    on an already-fetched series there, or via this function directly when a fresh live query is
    wanted instead."""
    cfg = config or {}
    windows = _windows(query(_resolve_kql(cfg)) or [])
    series_shaped = [{"ts": w["ts"], "cuPct": round(w["pct"], 1), **{k: v for k, v in w.items()
                     if k not in ("cap", "ts", "pct")}} for w in windows]
    return burndown_chain_from_series(series_shaped)
