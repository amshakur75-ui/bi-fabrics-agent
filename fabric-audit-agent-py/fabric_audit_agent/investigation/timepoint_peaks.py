"""Per-operation timepoint peak intensity — the Capacity Metrics app "Timepoint Detail" lens.

The Metrics app spreads an INTERACTIVE operation's CU over a 5-minute (10 x 30-second) smoothing
window, then reports, per timepoint, that slice against the 30-second base budget:

    timepointCuSeconds = totalCuSeconds / 10             # 5-min interactive smoothing (10 timepoints)
    pctBase = timepointCuSeconds / (baseCu * 30) * 100   # share of a 30-second timepoint budget

Validated against the app's Timepoint Detail on an F1024 capacity: an operation with 54,302.75
total CU-sec shows 5,430.2752 timepoint CU-sec and 17.68% of base — exactly this formula
(5,430.2752 / (1024 * 30) * 100 = 17.68). This is the lens a user means by "who hit a large % of
base capacity AT A MOMENT". It is NOT totalCu/base, which treats cumulative CU-seconds as if they
were instantaneous and yields impossible >100% figures for every long-running query.

Background/refresh operations smooth over 24h, not 5 minutes, so this interactive lens does not
apply to them — they are excluded by default (pass ``include_refresh=True`` to fold them in, but
their pctBase under this formula is not app-accurate and callers must label it).

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
    Returns ``None`` for trial / unknown / empty names (the caller then omits pctBase and says so),
    so a trial capacity like ``FTL64`` never silently produces a bogus percentage.
    """
    if not sku or not isinstance(sku, str):
        return None
    s = sku.strip().upper()
    m = _re.fullmatch(r"F(\d+)", s)
    if m:
        return int(m.group(1))
    return _P_SKU_BASE_CU.get(s)


def timepoint_pct_base(cu_seconds, base_cu):
    """The Metrics-app timepoint % of base for one interactive operation's total CU-seconds.
    Returns ``None`` when ``base_cu`` is unknown/non-positive."""
    if base_cu is None or base_cu <= 0 or cu_seconds is None:
        return None
    tp_cu = cu_seconds / _INTERACTIVE_SMOOTHING_TIMEPOINTS
    return tp_cu / (base_cu * _TIMEPOINT_SECONDS) * 100


def timepoint_peaks(events, *, base_cu, top_n=20, min_pct_base=None, include_refresh=False):
    """Rank operations by timepoint % of base capacity (the Metrics-app lens).

    ``events`` are normalized events (see ``investigation.events.normalize_event``): each carries
    ``ts``/``user``/``item``/``operation``/``kind``/``cuSeconds``/``durationMs``.

    Returns ``[{ts, user, item, operation, kind, durationMs, cuSeconds, timepointCuSeconds,
    pctBase}]`` sorted by pctBase descending (identical order to cuSeconds for a fixed base, so
    BOTH the "intensity" and the "size" ranking agree), truncated to ``top_n``, filtered to
    ``pctBase >= min_pct_base`` when that is set. ``pctBase``/``timepointCuSeconds`` are ``None``
    when ``base_cu`` is unknown (rows still returned, ranked by raw cuSeconds, so the caller can
    show sizes and say the % is unavailable). Refresh/background ops are excluded unless
    ``include_refresh`` — the interactive smoothing does not model their 24h spread.
    """
    rows = []
    for e in events or []:
        if not isinstance(e, dict):
            continue
        if not include_refresh and e.get("kind") == "refresh":
            continue
        cu = e.get("cuSeconds")
        if cu is None:
            continue
        pct = timepoint_pct_base(cu, base_cu)
        if min_pct_base is not None and (pct is None or pct < min_pct_base):
            continue
        tp_cu = (cu / _INTERACTIVE_SMOOTHING_TIMEPOINTS) if base_cu else None
        rows.append({
            "ts": e.get("ts"),
            "user": e.get("user"),
            "item": e.get("item"),
            "operation": e.get("operation"),
            "kind": e.get("kind"),
            "durationMs": e.get("durationMs"),
            "cuSeconds": round(cu, 4),
            "timepointCuSeconds": round(tp_cu, 4) if tp_cu is not None else None,
            "pctBase": round(pct, 2) if pct is not None else None,
        })
    # Rank by pctBase when available, else by raw cuSeconds — both give the same order for a fixed
    # base, so "biggest % of base" and "biggest CU" never disagree within one call.
    rows.sort(key=lambda r: r["pctBase"] if r["pctBase"] is not None else r["cuSeconds"], reverse=True)
    return rows[:top_n]
