from fabric_audit_agent.investigation.baseline import compute_baseline, compare_to_baseline


def test_compute_baseline_percentiles_and_opmix():
    rows = [{"cuSeconds": c, "operation": "query", "hourUtc": 14} for c in (10, 20, 30, 40, 100)]
    b = compute_baseline(rows)
    assert b["count"] == 5
    assert b["p50"] == 30
    assert b["p95"] >= 40 and b["p99"] >= b["p95"]
    assert b["opMix"] == {"query": 5}
    assert b["peakHourUtc"] == 14


def test_compute_baseline_empty():
    b = compute_baseline([])
    assert b["count"] == 0 and b["p50"] is None and b["peakHourUtc"] is None


def test_compare_to_baseline_flags_outlier():
    b = compute_baseline([{"cuSeconds": c} for c in (10, 20, 30, 40, 50)])
    today = compare_to_baseline(500, b)
    assert today["percentileRank"] == 100.0 and today["shifted"] is True


def test_compare_to_baseline_normal_run_not_shifted():
    b = compute_baseline([{"cuSeconds": c} for c in (10, 20, 30, 40, 50)])
    assert compare_to_baseline(30, b)["shifted"] is False
