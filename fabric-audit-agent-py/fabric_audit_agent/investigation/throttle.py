"""3-stage throttle decomposition — executable form of Microsoft's admin troubleshooting runbook
(capacity-planning-troubleshoot-throttling): (1) over-utilized? (2) did a throttling SIGNAL fire?
(3) which operations caused it. The stage-2 gate is the honesty core: CU%>100 alone NEVER
concludes "throttling" — only a fired signal (interactive delay/rejection, background rejection)
does; when the signal series isn't collected, the conclusion is explicitly "unconfirmed".
Pure + deterministic; series/events injected."""
from .expensive import top_expensive

_SIGNALS = (("interactiveDelay", "interactiveDelayPct"),
            ("interactiveRejection", "interactiveRejectionPct"),
            ("backgroundRejection", "backgroundRejectionPct"))


def _over_windows(series, threshold):
    runs, start, last = [], None, None
    for p in series:
        cu = p.get("cuPct")
        if isinstance(cu, (int, float)) and cu > threshold:
            start = start if start is not None else p.get("ts")
            last = p.get("ts")
        elif start is not None:
            runs.append([start, last]); start = None
    if start is not None:
        runs.append([start, last])
    return runs[:10]


def decompose_throttle(capacity_series, events, *, threshold=100.0, top_n=5, has_real_cost=True):
    series = capacity_series or []
    over = [p for p in series
            if isinstance(p.get("cuPct"), (int, float)) and p["cuPct"] > threshold]
    max_cu = max((p["cuPct"] for p in series if isinstance(p.get("cuPct"), (int, float))), default=None)
    windows = _over_windows(series, threshold)
    stage1 = {"maxCuPct": max_cu, "timepointsOver": len(over), "overWindows": windows}

    if not over:
        return {"stage1": stage1,
                "stage2": {"available": False, "skipped": True,
                            "note": "CU% never exceeded the threshold — slowness has another cause"},
                "stage3": None, "conclusion": "not-throttling",
                "thresholds": {"cuPct": threshold}}

    stage2, any_signal_present, fired = {}, False, False
    for name, field in _SIGNALS:
        vals = [p[field] for p in series if isinstance(p.get(field), (int, float))]
        if vals:
            any_signal_present = True
            sig_fired = max(vals) > 100.0
            fired = fired or sig_fired
            stage2[name] = {"fired": sig_fired, "maxPct": max(vals)}
    if not any_signal_present:
        stage2 = {"available": False,
                  "note": ("throttling-signal series not collected — CU%>100 alone does not prove "
                            "throttling fired; check the Capacity Metrics app Throttling tab "
                            "(stage-2 gate unavailable here)")}
    else:
        stage2["available"] = True

    in_window = [e for e in (events or [])
                 if any(w[0] <= (e.get("ts") or "") <= w[1] for w in windows)]
    tops = top_expensive(in_window, n=top_n)
    stage3 = {"topOperations": tops,
              "rankedBy": "cuSeconds" if has_real_cost else "arbitrary",
              "interactiveCount": sum(1 for e in in_window if e.get("kind") == "interactive"),
              "backgroundCount": sum(1 for e in in_window if e.get("kind") == "refresh")}
    if not has_real_cost:
        stage3["note"] = "operation-level data — per-query cost unavailable; drivers unranked"

    conclusion = ("throttling-confirmed" if (any_signal_present and fired)
                  else "over-utilized-unconfirmed")
    out = {"stage1": stage1, "stage2": stage2, "stage3": stage3,
           "conclusion": conclusion, "thresholds": {"cuPct": threshold}}
    # Burndown passthrough — the Metrics app's OWN figure, verbatim, never re-derived.
    burndown = [p["minutesToBurndown"] for p in series
                if isinstance(p.get("minutesToBurndown"), (int, float))]
    if burndown:
        out["minutesToBurndown"] = burndown[-1]
    return out
