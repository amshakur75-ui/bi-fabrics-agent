"""3-stage throttle decomposition — executable form of Microsoft's admin troubleshooting runbook
(capacity-planning-troubleshoot-throttling): (1) over-utilized? (2) did a throttling SIGNAL fire?
(3) which operations caused it. The stage-2 gate is the honesty core: CU%>100 alone NEVER
concludes "throttling" — only a fired signal (interactive delay/rejection, background rejection)
does; when the signal series isn't collected, the conclusion is explicitly "unconfirmed".
Pure + deterministic; series/events injected.

STAGE 2 CURRENTLY HAS NO USABLE SIGNAL SOURCE. The three ``*Pct`` fields it reads are the
capacity's threshold SETTINGS (constant), not utilization against them, so they can no longer
fire — see the retirement note in the loop below. Every conclusion is therefore
"over-utilized-unconfirmed" until a genuine signal source is wired. That is the honest answer,
not a regression: the alternative was "throttling-confirmed" on every burst."""
import math

from .expensive import top_expensive

_SIGNALS = (("interactiveDelay", "interactiveDelayPct"),
            ("interactiveRejection", "interactiveRejectionPct"),
            ("backgroundRejection", "backgroundRejectionPct"))


def _num(v):
    # mirrors JS Number.isFinite: rejects bool, NaN, and Infinity
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _over_windows(series, threshold):
    """Every contiguous over-threshold run, UNCAPPED -- callers that need the display-only
    max-10 cap slice it themselves (stage1["overWindows"]); stage-3 driver ranking must see
    every run so events in windows 11+ don't silently vanish from "who caused it"."""
    runs, start, last = [], None, None
    for p in series:
        cu = p.get("cuPct")
        if _num(cu) and cu > threshold:
            start = start if start is not None else p.get("ts")
            last = p.get("ts")
        elif start is not None:
            runs.append([start, last]); start = None
    if start is not None:
        runs.append([start, last])
    return runs


def decompose_throttle(capacity_series, events, *, threshold=100.0, top_n=5, has_real_cost=True):
    series = capacity_series or []
    over = [p for p in series
            if _num(p.get("cuPct")) and p["cuPct"] > threshold]
    max_cu = max((p["cuPct"] for p in series if _num(p.get("cuPct"))), default=None)
    windows = _over_windows(series, threshold)
    stage1 = {"maxCuPct": max_cu, "timepointsOver": len(over), "overWindows": windows[:10]}

    # NO DATA IS NOT "NOT THROTTLING". max_cu is None when the series is empty or every point's
    # cuPct is unusable, which is indistinguishable from a genuinely calm capacity once you only
    # look at `over`. Reached live from capacity_diagnostics: a zero-row pull raises nothing, so no
    # error is recorded and the agent reads "slowness has another cause" as a POSITIVE finding --
    # steering an admin away from the cause during a real 247% event, with a citation. Stage 2 of
    # this same module already refuses to answer when its series was not collected; stage 1 had no
    # equivalent.
    if max_cu is None:
        return {"stage1": stage1,
                "stage2": {"available": False,
                           "note": "no CU readings in the window — nothing could be evaluated"},
                "stage3": None, "conclusion": "unknown-no-data",
                "note": ("No usable capacity readings were returned for this window, so this is NOT "
                         "evidence that the capacity was healthy — the source may be empty, lagging, "
                         "unauthorized or misconfigured. Re-run with a wider window or check the "
                         "capacity-events stream before concluding anything about throttling."),
                "thresholds": {"cuPct": threshold}}

    if not over:
        return {"stage1": stage1,
                "stage2": {"available": False, "skipped": True,
                            "note": "CU% never exceeded the threshold — slowness has another cause"},
                "stage3": None, "conclusion": "not-throttling",
                "thresholds": {"cuPct": threshold}}

    stage2, any_signal_present, fired = {}, False, False
    for name, field in _SIGNALS:
        vals = [p[field] for p in series if _num(p.get(field))]
        if vals:
            # RETIRED as a firing signal (2026-08-10). These three fields come from the
            # CapacityEvents ``*ThresholdPercentage`` columns, which kb/metric_definitions.py
            # classifies as metric_type "reference" and confirms CONSTANT = 1 -- they are the
            # capacity's throttling SETTINGS, not its utilization against them. The live value
            # 1.237113, scaled x100 by the collector, is 123.71, so ``max(vals) > 100.0`` was
            # TRUE ON EVERY WINDOW. That turned the stage-2 honesty gate into a rubber stamp:
            # any incident with CU>100 concluded "throttling-confirmed" citing "maxPct 123.71%",
            # on the chat surface a human actually asks, for what Microsoft's own guidance says
            # is smoothing absorbing a burst. tier2_check._check_throttle_imminent was retired
            # for exactly this misread; the retirement never propagated here.
            # We still REPORT the values (they are real settings) but they can no longer fire.
            # any_signal_present stays True so these entries survive: it distinguishes "the series
            # carried these columns" (-> report them, explain why they cannot answer) from "the
            # columns were never collected" (-> the pre-existing not-collected note). `fired` is
            # never set, so `conclusion` can only be over-utilized-unconfirmed either way.
            any_signal_present = True
            stage2[name] = {"fired": False, "maxPct": max(vals), "retired": True,
                            "note": ("threshold SETTING, not utilization (constant) — cannot "
                                     "confirm throttling")}
    if not any_signal_present:
        stage2 = {"available": False,
                  "note": ("throttling-signal series not collected — CU%>100 alone does not prove "
                            "throttling fired; check the Capacity Metrics app Throttling tab "
                            "(stage-2 gate unavailable here)")}
    else:
        # available=False even though values were present: a retired signal cannot ANSWER the
        # stage-2 question, so claiming the gate ran would be the same lie in a quieter voice.
        stage2["available"] = False
        stage2["note"] = ("the only throttling-signal fields collected are threshold SETTINGS "
                          "(constant), not utilization — stage-2 cannot confirm throttling; check "
                          "the Capacity Metrics app Throttling tab")

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
                if _num(p.get("minutesToBurndown"))]
    if burndown:
        out["minutesToBurndown"] = burndown[-1]
    return out
