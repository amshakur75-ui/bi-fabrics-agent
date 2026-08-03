"""Capacity-level over-threshold windows, with contributors.

Answers "when did total CU% cross 100% / 1000%, and who contributed?" -- a CAPACITY-level question,
distinct from any single operation's % of base.

Approach (uses only known-good data, so it is offline-testable and doesn't depend on the capacity
stream carrying a per-workload split):
  * TOTAL CU% per 30-second window comes from the capacity utilization stream (capacityUnitMs /
    (base * 30000) * 100) -- the authoritative-ish number the Metrics app is built on.
  * INTERACTIVE CU% is derived from the user operations we can attribute (their CU-seconds spread
    linearly across the 30-second windows each operation overlaps, over base*30).
  * BACKGROUND CU% is the residual: max(0, total - interactive). A window with high total but low
    interactive is background/system-driven (refresh, dataflow, OneLake, ML) -- NOT a user's fault.
  * CONTRIBUTORS for a hot window are the user operations overlapping it, ranked by the CU they
    contributed to that window.

Time math is on epoch seconds so the core is pure/stdlib and testable without a clock.
"""
_WINDOW_SEC = 30


def window_start(epoch):
    """Floor an epoch-second value to its 30-second window start."""
    return int(epoch // _WINDOW_SEC) * _WINDOW_SEC


def _op_window_contributions(op):
    """Yield (windowStartEpoch, cuInWindow) for one op, spreading its cuSeconds linearly across the
    30-second windows its [startEpoch, endEpoch] overlaps. A zero/negative-duration op lands wholly
    in its start window."""
    start = op.get("startEpoch")
    end = op.get("endEpoch")
    cu = op.get("cuSeconds") or 0.0
    if start is None or end is None:
        return
    if end <= start:
        yield window_start(start), cu
        return
    total = end - start
    w = window_start(start)
    while w < end:
        seg = min(end, w + _WINDOW_SEC) - max(start, w)
        if seg > 0:
            yield w, cu * (seg / total)
        w += _WINDOW_SEC


def overload_windows(series, ops, *, base_cu, min_cu_pct=100.0, top_windows=50,
                     top_contributors=5):
    """Return capacity windows at/over ``min_cu_pct`` total CU%, decomposed with contributors.

    ``series``: [{"epoch": <window start epoch sec>, "cuPct": <total CU% for that window>}] (the
                capacity utilization stream, already reduced to per-window total CU%).
    ``ops``:    [{"startEpoch", "endEpoch", "cuSeconds", "user", "item", "operation"}] user ops.
    ``base_cu``: base capacity units (F1024 -> 1024); interactive% is None when unknown.

    Returns [{windowEpoch, totalCuPct, interactiveCuPct, backgroundCuPct, contributors:[{user,
    item, operation, cuInWindow}]}] sorted by totalCuPct desc, truncated to ``top_windows``.
    interactive/background are None when base_cu is unknown (can't convert CU-sec to %).
    """
    # Interactive CU-seconds + contributing ops per window, from the attributable operations.
    by_window = {}
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        for w, cu_in in _op_window_contributions(op):
            slot = by_window.setdefault(w, {"cu": 0.0, "ops": []})
            slot["cu"] += cu_in
            slot["ops"].append({
                "user": op.get("user"), "item": op.get("item"),
                "operation": op.get("operation"), "cuInWindow": round(cu_in, 3),
            })

    budget = (base_cu * _WINDOW_SEC) if base_cu else None   # CU-seconds a 30-s window can bill
    out = []
    for pt in series or []:
        if not isinstance(pt, dict):
            continue
        total = pt.get("cuPct")
        if total is None or total < min_cu_pct:
            continue
        w = pt.get("epoch")
        slot = by_window.get(window_start(w) if w is not None else None, {"cu": 0.0, "ops": []})
        interactive = (slot["cu"] / budget * 100) if budget else None
        background = max(0.0, total - interactive) if interactive is not None else None
        contributors = sorted(slot["ops"], key=lambda o: o["cuInWindow"], reverse=True)[:top_contributors]
        out.append({
            "windowEpoch": w,
            "totalCuPct": round(total, 1),
            "interactiveCuPct": round(interactive, 1) if interactive is not None else None,
            "backgroundCuPct": round(background, 1) if background is not None else None,
            "contributors": contributors,
        })
    out.sort(key=lambda r: r["totalCuPct"], reverse=True)
    return out[:top_windows]
