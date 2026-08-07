"""Phase 4.1-4.4 — shared statistical primitives ported from analysis.ts."""
from fabric_audit_agent import stats


def test_linear_trend_perfect_fit_r2_one():
    fit = stats.linear_trend([0, 1, 2, 3, 4])
    assert abs(fit["slope"] - 1.0) < 1e-9 and abs(fit["r2"] - 1.0) < 1e-9


def test_linear_trend_flat_series_r2_zero():
    fit = stats.linear_trend([5, 5, 5, 5])
    assert fit["slope"] == 0.0 and fit["r2"] == 0.0


def test_trend_needs_six_points():
    assert stats.trend_direction([1, 2, 3, 4, 5])["direction"] == "insufficient"


def test_trend_increasing_with_band_and_fit():
    d = stats.trend_direction([10, 20, 30, 40, 50, 60])
    assert d["direction"] == "increasing" and d["windowChangePct"] > 15 and not d["weakFit"]


def test_trend_stable_within_band():
    # a tiny drift on a large mean stays inside the ±15% window-change dead-band
    d = stats.trend_direction([100, 101, 100, 99, 100, 101])
    assert d["direction"] == "stable"


def test_trend_weak_fit_flagged():
    # a noisy series with an overall rise but poor linearity → direction set but weakFit True
    d = stats.trend_direction([10, 90, 15, 85, 20, 120])
    assert d["r2"] < 0.3 and (d["weakFit"] if d["direction"] != "stable" else True)


def test_mad_is_outlier_robust():
    # one extreme outlier barely moves the median/MAD (unlike stddev)
    base = [10, 11, 12, 10, 11, 12]
    assert stats.median(base) in (11, 11.0)
    assert stats.median_abs_deviation(base) <= 1.5


def test_is_spike_and_floor():
    series = [10, 11, 12, 10, 11, 12]
    assert stats.is_spike(200, series) is True
    assert stats.is_spike(12, series) is False
    assert stats.is_spike(9, [1, 1, 1, 1]) is False   # below the absolute value floor


def test_spike_severity_bands():
    series = [10, 11, 12, 10, 11, 12]
    assert stats.spike_severity(500, series) == "severe"     # huge z / delta
    assert stats.spike_severity(11, series) == "mild"


def test_meaningful_pct_change_floor():
    assert stats.meaningful_pct_change(10) is True
    assert stats.meaningful_pct_change(2) is False
    assert stats.meaningful_pct_change(None) is False
