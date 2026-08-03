from fabric_audit_agent.investigation.forecast_throttle import forecast_time_to_threshold


def _series(vals, start_min=0, step=1):
    return [{"ts": f"2026-07-07T{(start_min+i*step)//60:02d}:{(start_min+i*step)%60:02d}:00Z",
             "cuPct": v} for i, v in enumerate(vals)]


def test_linear_climb_projects_to_threshold():
    s = _series([50 + 2*i for i in range(10)])       # +2 pct/min, at 68% after 9 min
    out = forecast_time_to_threshold(s)
    assert out["minutesToThreshold"] is not None
    assert 15.0 <= out["minutesToThreshold"] <= 17.0  # (100-68)/2 = 16 min from the last point
    assert out["method"] == "robust-trend"


def test_flat_or_falling_returns_none_with_basis():
    out = forecast_time_to_threshold(_series([70.0] * 10))
    assert out["minutesToThreshold"] is None and "not rising" in out["basis"]


def test_already_over_threshold_returns_zero():
    out = forecast_time_to_threshold(_series([90, 95, 101, 105]))
    assert out["minutesToThreshold"] == 0.0


def test_too_few_points_returns_none():
    out = forecast_time_to_threshold(_series([50, 60]))
    assert out["minutesToThreshold"] is None and "points" in out["basis"]


def test_outlier_resistant():
    vals = [50 + 2*i for i in range(10)]; vals[4] = 500.0   # single spike must not wreck the slope
    out = forecast_time_to_threshold(_series(vals))
    assert out["minutesToThreshold"] is not None and out["minutesToThreshold"] < 60
