"""Per-operation capacity-peak intensity — TWO lenses, both surfaced on every row.

A single operation's cost can be read two legitimate ways, and users want both:

1. LIFETIME lens (a.k.a. operation cost):   pctBaseLifetime = cuSeconds / baseCu * 100
   "CPU-seconds this operation burned, as a multiple of one second of full base capacity."
   A 6-minute MDX query burning 4,825 CU-sec on F1024 reads 471% -- it consumed ~4.7 seconds of
   full-capacity compute over its life. This is the lens for "which operations are expensive" and
   for thresholding (>100% / >300% / >1000%). It is NOT a moment-in-time utilization; a long query
   is spread over its whole duration.

2. TIMEPOINT lens (matches the Capacity Metrics app "Timepoint Detail" column):
       timepointCuSeconds = cuSeconds / 10              # 5-min interactive smoothing (10 timepoints)
       pctBaseTimepoint   = timepointCuSeconds / (baseCu * 30) * 100
   Validated against a real F1024 Timepoint Detail screenshot: 54,302.75 total CU-sec ->
   5,430.2752 timepoint -> 17.68% of base, exact. This is the lens for "what share of a 30-second
   window did this hold" and for reconciling against the Metrics app.

Both are monotonic in cuSeconds, so a ranking by either yields the SAME order -- only the threshold
cutoff differs (300% lifetime is a big query; 30% timepoint is a big 30-second slice). Callers show
both columns and threshold on whichever lens the question implies (default: lifetime, the "expensive
operations" workflow).

base_cu_from_sku maps a SKU to base capacity units; None for trial/unknown (both pcts omitted).
Pure/stdlib.
"""
import re as _re

_INTERACTIVE_SMOOTHING_TIMEPOINTS = 10   # 5 minutes / 30-second timepoints
_TIMEPOINT_SECONDS = 30

# P-SKU base capacity units (P_n = 64 * 2**(n-1)).
_P_SKU_BASE_CU = {"P1": 64, "P2": 128, "P3": 256, "P4": 512, "P5": 1024}


def base_cu_from_sku(sku):
    """Base capacity units for a SKU name.

    F-SKU: the integer in the name (``F1024`` -> 1024). P-SKU: ``P1``..``P5`` -> 64..1024.
    Returns ``None`` for trial / unknown / empty names (the caller then omits the % columns and says
    so), so a trial capacity like ``FTL64`` never silently produces a bogus percentage.
    """
    if not sku or not isinstance(sku, str):
        return None
    s = sku.strip().upper()
    m = _re.fullmatch(r"F(\d+)", s)
    if m:
        return int(m.group(1))
    return _P_SKU_BASE_CU.get(s)


def lifetime_pct_base(cu_seconds, base_cu):
    """Operation-lifetime % of base: cuSeconds / baseCu * 100 (the '471%' operation-cost lens).
    Returns ``None`` when base is unknown/non-positive."""
    if base_cu is None or base_cu <= 0 or cu_seconds is None:
        return None
    return cu_seconds / base_cu * 100


def timepoint_pct_base(cu_seconds, base_cu):
    """Metrics-app timepoint % of base for one interactive op: (cuSeconds/10) / (baseCu*30) * 100.
    Returns ``None`` when base is unknown/non-positive."""
    if base_cu is None or base_cu <= 0 or cu_seconds is None:
        return None
    tp_cu = cu_seconds / _INTERACTIVE_SMOOTHING_TIMEPOINTS
    return tp_cu / (base_cu * _TIMEPOINT_SECONDS) * 100


def timepoint_peaks(events, *, base_cu, top_n=20, min_pct=None, lens="lifetime",
                    include_refresh=False):
    """Rank operations by cost and surface BOTH the lifetime and timepoint % of base per row.

    ``events`` are normalized events (see ``investigation.events.normalize_event``): each carries
    ``ts``/``user``/``item``/``operation``/``kind``/``cuSeconds``/``durationMs``.

    Returns ``[{ts, user, item, operation, kind, durationMs, cuSeconds, pctBaseLifetime,
    timepointCuSeconds, pctBaseTimepoint}]`` sorted by ``cuSeconds`` descending (identical order to
    either % column), truncated to ``top_n``. When ``min_pct`` is set, keeps only rows whose
    ``lens`` percentage ('lifetime' -> pctBaseLifetime, 'timepoint' -> pctBaseTimepoint) is
    >= ``min_pct``. Percentages are ``None`` when ``base_cu`` is unknown (rows still returned,
    ranked by raw cuSeconds, so the caller can show sizes and say the % is unavailable). Refresh/
    background ops are excluded unless ``include_refresh`` (the timepoint smoothing does not model
    their 24h spread; their lifetime % is still meaningful, so a caller wanting them passes True).
    """
    if lens not in ("lifetime", "timepoint"):
        raise ValueError(f"lens must be 'lifetime' or 'timepoint', got {lens!r}")
    rows = []
    for e in events or []:
        if not isinstance(e, dict):
            continue
        if not include_refresh and e.get("kind") == "refresh":
            continue
        cu = e.get("cuSeconds")
        if cu is None:
            continue
        pct_life = lifetime_pct_base(cu, base_cu)
        pct_tp = timepoint_pct_base(cu, base_cu)
        chosen = pct_life if lens == "lifetime" else pct_tp
        if min_pct is not None and (chosen is None or chosen < min_pct):
            continue
        rows.append({
            "ts": e.get("ts"),
            "user": e.get("user"),
            "item": e.get("item"),
            "operation": e.get("operation"),
            "operationDetail": e.get("operationDetail"),
            "kind": e.get("kind"),
            "durationMs": e.get("durationMs"),
            "cuSeconds": round(cu, 4),
            "pctBaseLifetime": round(pct_life, 1) if pct_life is not None else None,
            "timepointCuSeconds": round(cu / _INTERACTIVE_SMOOTHING_TIMEPOINTS, 4) if base_cu else None,
            "pctBaseTimepoint": round(pct_tp, 2) if pct_tp is not None else None,
        })
    rows.sort(key=lambda r: r["cuSeconds"], reverse=True)
    return rows[:top_n]
