"""3-stage throttle decomposition (Microsoft admin-runbook gate). Pure + deterministic."""
from fabric_audit_agent.investigation.throttle import decompose_throttle

_SERIES_CALM = [{"ts": f"2026-07-07T09:{m:02d}:00Z", "cuPct": 60.0} for m in range(10)]
_SERIES_HOT = ([{"ts": "2026-07-07T09:00:00Z", "cuPct": 80.0}]
               + [{"ts": f"2026-07-07T09:{m:02d}:00Z", "cuPct": 130.0} for m in (1, 2, 3)]
               + [{"ts": "2026-07-07T09:04:00Z", "cuPct": 70.0}])
_EVENTS = [
    {"ts": "2026-07-07T09:02:00Z", "user": "john@co", "item": "Sales", "kind": "interactive", "cuSeconds": 90.0},
    {"ts": "2026-07-07T09:02:30Z", "user": "svc@co", "item": "Sales Model", "kind": "refresh", "cuSeconds": 40.0},
    {"ts": "2026-07-07T08:00:00Z", "user": "amy@co", "item": "HR", "kind": "interactive", "cuSeconds": 500.0},  # outside window
]

def test_stage1_calm_series_concludes_not_throttling_and_skips_stages():
    out = decompose_throttle(_SERIES_CALM, _EVENTS)
    assert out["conclusion"] == "not-throttling"
    assert out["stage1"]["timepointsOver"] == 0
    assert out["stage2"]["available"] is False or out["stage2"].get("skipped") is True
    assert out["stage3"] is None

def test_stage2_unavailable_gives_unconfirmed_not_confirmed():
    out = decompose_throttle(_SERIES_HOT, _EVENTS)
    assert out["conclusion"] == "over-utilized-unconfirmed"     # CU%>100 alone NEVER confirms
    assert out["stage2"]["available"] is False
    assert "CU%>100 alone" in out["stage2"]["note"]

def test_the_threshold_signal_is_retired_and_can_no_longer_confirm_throttling():
    """The three *Pct fields stage 2 reads are the capacity's threshold SETTINGS (constant), not
    utilization against them. kb/metric_definitions.py classifies them metric_type "reference" and
    confirms constant = 1; the live value 1.237113 is scaled x100 by the collector to 123.71, so
    the old ``max(vals) > 100.0`` test was TRUE ON EVERY WINDOW. That made the stage-2 honesty gate
    -- whose entire purpose is that CU%>100 alone never concludes throttling -- a rubber stamp:
    every burst over 100% concluded "throttling-confirmed" citing "maxPct 123.71%", on the chat
    surface a human actually asks. tier2_check._check_throttle_imminent was retired for this exact
    misread; the retirement had never propagated here."""
    hot = [{**p, "interactiveDelayPct": 123.71} for p in _SERIES_HOT]
    out = decompose_throttle(hot, _EVENTS)
    assert out["conclusion"] == "over-utilized-unconfirmed"
    assert out["stage2"]["interactiveDelay"]["fired"] is False
    assert out["stage2"]["interactiveDelay"]["retired"] is True
    # The value is still reported (it IS a real setting) -- it just cannot answer the question.
    assert out["stage2"]["interactiveDelay"]["maxPct"] == 123.71
    # And the gate must not claim to have run: that would be the same lie in a quieter voice.
    assert out["stage2"]["available"] is False


def test_no_series_value_of_the_retired_signal_can_confirm_throttling():
    """Belt and braces: sweep the plausible range, including the live constant."""
    for v in (0.5, 80.0, 100.0, 100.1, 123.71, 500.0):
        out = decompose_throttle([{**p, "interactiveDelayPct": v} for p in _SERIES_HOT], _EVENTS)
        assert out["conclusion"] == "over-utilized-unconfirmed", v

def test_stage3_ranks_only_events_inside_over_windows():
    out = decompose_throttle(_SERIES_HOT, _EVENTS)
    tops = out["stage3"]["topOperations"]
    assert [t["user"] for t in tops][:2] == ["john@co", "svc@co"]   # amy (08:00) excluded
    assert out["stage3"]["interactiveCount"] == 1 and out["stage3"]["backgroundCount"] == 1

def test_over_window_boundaries_reported():
    out = decompose_throttle(_SERIES_HOT, _EVENTS)
    assert out["stage1"]["overWindows"] == [["2026-07-07T09:01:00Z", "2026-07-07T09:03:00Z"]]

def test_burndown_surfaced_verbatim_when_present():
    series = [{**p, "minutesToBurndown": 42.0} for p in _SERIES_HOT]
    out = decompose_throttle(series, _EVENTS)
    assert out["minutesToBurndown"] == 42.0

def test_has_real_cost_false_ranks_stage3_as_arbitrary():
    out = decompose_throttle(_SERIES_HOT, _EVENTS, has_real_cost=False)
    assert out["stage3"]["rankedBy"] == "arbitrary"
    assert "note" in out["stage3"]


def _series_with_n_over_runs(n):
    """n contiguous single-point over-threshold runs, each separated by a calm point, so
    each run becomes its own window: [over, calm, over, calm, ...]."""
    series = []
    for i in range(n):
        series.append({"ts": f"2026-07-07T09:{2 * i:02d}:00Z", "cuPct": 130.0})
        series.append({"ts": f"2026-07-07T09:{2 * i + 1:02d}:00Z", "cuPct": 60.0})
    return series


def test_stage3_uses_full_uncapped_windows_beyond_display_cap_of_10():
    # 11 over-threshold runs -> stage1["overWindows"] is capped at 10 (display-only), but
    # an event inside run #11 must still be counted for stage-3 driver ranking.
    series = _series_with_n_over_runs(11)
    run11_ts = series[20]["ts"]   # the 11th over-threshold point (index 2*10)
    assert series[20]["cuPct"] == 130.0
    events = [{"ts": run11_ts, "user": "late@co", "item": "Late Item",
               "kind": "interactive", "cuSeconds": 10.0}]
    out = decompose_throttle(series, events, top_n=20)
    assert len(out["stage1"]["overWindows"]) == 10
    users = [t["user"] for t in out["stage3"]["topOperations"]]
    assert "late@co" in users


def test_bool_cu_pct_point_skipped_not_treated_as_one():
    series = [{"ts": "2026-07-07T09:00:00Z", "cuPct": True},
              {"ts": "2026-07-07T09:01:00Z", "cuPct": 60.0}]
    out = decompose_throttle(series, [])
    assert out["stage1"]["timepointsOver"] == 0
    assert out["stage1"]["maxCuPct"] == 60.0
    assert out["conclusion"] == "not-throttling"


def test_nan_cu_pct_point_skipped():
    series = [{"ts": "2026-07-07T09:00:00Z", "cuPct": float("nan")},
              {"ts": "2026-07-07T09:01:00Z", "cuPct": 60.0}]
    out = decompose_throttle(series, [])
    assert out["stage1"]["timepointsOver"] == 0
    assert out["stage1"]["maxCuPct"] == 60.0


def test_bool_minutes_to_burndown_not_surfaced():
    series = [{**p, "minutesToBurndown": True} for p in _SERIES_HOT]
    out = decompose_throttle(series, _EVENTS)
    assert "minutesToBurndown" not in out
