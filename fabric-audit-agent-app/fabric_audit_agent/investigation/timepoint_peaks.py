"""Per-operation capacity-peak intensity — ONE lens (the timepoint lens was retired, see below).

LIFETIME lens (operation cost):   pctBaseLifetime = cuSeconds / baseCu * 100
"CPU-seconds this operation burned, as a multiple of one second of full base capacity." A 6-minute
MDX query burning 4,825 CU-sec on F1024 reads 471% -- it consumed ~4.7 seconds of full-capacity
compute over its life. This is the lens for "which operations are expensive" and for thresholding
(>100% / >300% / >1000%). It is NOT a moment-in-time utilization; a long query is spread over its
whole duration. It is a PROXY intensity (cuSeconds is CPU-time, not capacity CU) — see the system
prompt's true-CU-vs-proxy principle; it is NOT reconciled to the Capacity Metrics app.

RETIRED — the TIMEPOINT lens (``pctBaseTimepoint`` = (cuSeconds/10)/(baseCu*30)*100) existed ONLY to
reproduce the Capacity Metrics app's "Timepoint Detail" cell from the CpuTimeMs proxy. That goal is
abandoned (a proxy cannot match the app's true CU), so the timepoint field + the ``lens`` choice were
removed to kill the dual-lens confusion. See GAPS-AND-ISSUES.md ("Step 4: timepoint-lens retirement").

base_cu_from_sku maps a SKU to base capacity units; None for trial/unknown (pct omitted). Pure/stdlib.
"""
import re as _re

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


def timepoint_peaks(events, *, base_cu, top_n=20, min_pct=None, lens="lifetime",
                    include_refresh=False):
    """Rank operations by cost and surface the lifetime % of base (proxy intensity) per row.

    ``events`` are normalized events (see ``investigation.events.normalize_event``): each carries
    ``ts``/``user``/``item``/``operation``/``kind``/``cuSeconds``/``durationMs``.

    Returns ``[{ts, user, item, operation, kind, durationMs, cuSeconds, pctBaseLifetime,
    pctBaseConverted}]`` sorted by ``cuSeconds`` descending, truncated to ``top_n``. When ``min_pct``
    is set, keeps only rows whose ``pctBaseLifetime`` >= ``min_pct``. Percentages are ``None`` when
    ``base_cu`` is unknown (rows still returned, ranked by raw cuSeconds). Refresh/background ops are
    excluded unless ``include_refresh``.

    ``lens`` is retained for signature compatibility but only ``"lifetime"`` is supported — the
    timepoint lens (which existed only to match the Capacity Metrics app from the proxy) was retired.
    """
    if lens != "lifetime":
        raise ValueError(
            f"lens must be 'lifetime' ('timepoint' was retired — the proxy is not app-comparable), got {lens!r}")
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
        if min_pct is not None and (pct_life is None or pct_life < min_pct):
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
            # Readable 2-digit intensity = lifetime / 10 (e.g. 471.2% -> 47.1%). A PROXY intensity
            # view only — never reconciled to the Capacity Metrics app (see the true-CU-vs-proxy
            # principle); the retired pctBaseTimepoint used to claim that role.
            "pctBaseConverted": round(pct_life / 10, 1) if pct_life is not None else None,
        })
    rows.sort(key=lambda r: r["cuSeconds"], reverse=True)
    return rows[:top_n]
