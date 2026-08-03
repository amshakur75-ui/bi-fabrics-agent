from fabric_audit_agent.anomaly import detect_anomalies
from fabric_audit_agent.correlate import correlate
from fabric_audit_agent.forecast import forecast_capacity
from fabric_audit_agent.stagger import plan_stagger
from fabric_audit_agent.whatif import assess_what_if
from fabric_audit_agent.dax import analyze_dax


# ---- anomaly ----
def test_anomaly_flags_outlier():
    hist = [{"metrics": {"peakCuPct": p}} for p in (50, 52, 48, 51)]
    a = detect_anomalies({"capacity": {"capacityId": "F64", "peakCuPct": 95}}, hist, z=2)
    assert len(a) == 1 and a[0]["metric"] == "peakCuPct" and a[0]["direction"] == "above"
    assert "anomalous" in a[0]["message"]


def test_anomaly_insufficient_points_or_flat():
    assert detect_anomalies({"capacity": {"peakCuPct": 95}}, [{"metrics": {"peakCuPct": 50}}]) == []   # < minPoints
    assert detect_anomalies({"capacity": {"peakCuPct": 50}}, [{"metrics": {"peakCuPct": 50}}] * 5) == []   # stddev 0


# ---- correlate ----
def test_correlate_capacity_pressure():
    c = correlate([{"key": "capacity.throttle::x"}, {"key": "capacity.contention::y"}, {"key": "capacity.oversized-model::z"}])
    cp = next(x for x in c if x["theme"] == "capacity-pressure")
    assert set(cp["findingKeys"]) == {"capacity.throttle::x", "capacity.contention::y", "capacity.oversized-model::z"}


def test_correlate_security_cluster_needs_two():
    assert correlate([{"key": "security.admin-grant::a"}]) == []
    assert any(x["theme"] == "security-cluster" for x in correlate([{"key": "security.admin-grant::a"}, {"key": "security.external-share::b"}]))


# ---- forecast ----
def test_forecast_rising_to_ceiling():
    f = forecast_capacity([{"metrics": {"peakCuPct": p}} for p in (70, 80, 90)], ceiling=100)
    assert f["trend"] == "rising" and f["slopePerRun"] == 10 and f["runsToCeiling"] == 1
    assert "reaches 100%" in f["message"]


def test_forecast_insufficient_and_flat():
    assert forecast_capacity([{"metrics": {"peakCuPct": 50}}])["trend"] == "insufficient-data"
    f = forecast_capacity([{"metrics": {"peakCuPct": 50}}] * 3)
    assert f["trend"] == "flat" and f["runsToCeiling"] is None


# ---- stagger ----
def test_stagger_pushes_out_colliders_largest_keeps_slot():
    facts = {"capacity": {"refreshes": [
        {"dataset": "Big", "workspace": "W", "scheduledAt": "06:00", "sizeGB": 5},
        {"dataset": "Mid", "workspace": "W", "scheduledAt": "06:00", "sizeGB": 3},
        {"dataset": "Small", "workspace": "W", "scheduledAt": "06:00", "sizeGB": 1},
    ]}}
    moves = {p["dataset"]: p["to"] for p in plan_stagger(facts, spacing_min=15)}
    assert "Big" not in moves and moves["Mid"] == "06:15" and moves["Small"] == "06:30"


def test_stagger_ignores_singletons():
    assert plan_stagger({"capacity": {"refreshes": [{"dataset": "A", "workspace": "W", "scheduledAt": "06:00", "sizeGB": 1}]}}) == []


# ---- whatif ----
def test_whatif_blocked_when_high_risk():
    facts = {"capacity": {"capacityId": "F64", "peakCuPct": 95, "refreshes": [{"scheduledAt": "06:00"}, {"scheduledAt": "06:00"}]}}
    r = assess_what_if(facts, {"kind": "model", "sizeGB": 6, "refreshAt": "06:00"})
    assert r["verdict"] == "blocked" and r["riskScore"] >= 4


def test_whatif_safe_when_clear():
    r = assess_what_if({"capacity": {"capacityId": "F64", "peakCuPct": 40, "refreshes": []}}, {"kind": "model", "sizeGB": 1, "refreshAt": "03:00"})
    assert r["verdict"] == "safe" and r["riskScore"] == 0


# ---- dax ----
def test_dax_detects_patterns():
    assert "repeated-calculate" in {s["pattern"] for s in analyze_dax("CALCULATE( SUM(x) ) + CALCULATE( SUM(y) )")}
    assert "raw-division" in {s["pattern"] for s in analyze_dax("Revenue / Count")}
    assert "earlier" in {s["pattern"] for s in analyze_dax("EARLIER(Table[Col])")}


def test_dax_slow_no_pattern_fallback():
    s = analyze_dax("SUM(Sales[Amount])", {"durationMs": 8000})
    assert len(s) == 1 and s[0]["pattern"] == "slow-no-obvious-cause" and "8000 ms" in s[0]["suggestion"]


def test_dax_divide_url_and_below_threshold_not_flagged():
    assert analyze_dax("DIVIDE(a, b)") == []            # DIVIDE is the recommended fix, not flagged
    assert analyze_dax("http://example.com") == []      # '://' is not raw division
    assert analyze_dax("SUM(x)", {"durationMs": 4000}) == []   # < 5000 -> no fallback


def test_whatif_oversized_only_is_safe():
    r = assess_what_if({"capacity": {"capacityId": "F", "peakCuPct": 40, "refreshes": []}}, {"kind": "model", "sizeGB": 10, "refreshAt": "03:00"})
    assert r["riskScore"] == 1 and r["verdict"] == "safe"   # +1 only -> safe (< 2)


def test_correlate_throttle_alone_no_pressure():
    assert correlate([{"key": "capacity.throttle::x"}]) == []   # no drivers -> no capacity-pressure theme
