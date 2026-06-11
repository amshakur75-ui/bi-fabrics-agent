from fabric_audit_agent.severity import score_severity
from fabric_audit_agent.config import merge_config


def sev(t, **ev):
    return score_severity({"type": t, "evidence": ev})


def test_capacity_throttle():
    assert sev("capacity.throttle", peakCuPct=85, throttleMinutes=5)["level"] == "Warning"
    assert sev("capacity.throttle", peakCuPct=95, throttleMinutes=40)["level"] == "Critical"


def test_capacity_contention():
    assert sev("capacity.contention", datasets=["a", "b", "c"], time="06:00")["level"] == "Warning"
    s = sev("capacity.contention", datasets=["a", "b", "c", "d"], time="06:00")
    assert s["level"] == "Critical" and "4 models refresh at 06:00" in s["reason"]


def test_capacity_oversized_model():
    assert sev("capacity.oversized-model", sizeGB=20, memoryGB=64)["level"] == "Critical"  # 20 >= 25% of 64
    assert sev("capacity.oversized-model", sizeGB=5, memoryGB=64)["level"] == "Warning"


def test_capacity_concentration():
    assert sev("capacity.concentration", sharePct=65)["level"] == "Critical"
    assert sev("capacity.concentration", sharePct=35)["level"] == "Warning"


def test_model_bidirectional():
    assert sev("model.bidirectional", count=9)["level"] == "Critical"
    assert sev("model.bidirectional", count=4)["level"] == "Warning"


def test_model_auto_datetime():
    assert sev("model.auto-datetime")["level"] == "Warning"


def test_model_refresh_failing():
    assert sev("model.refresh-failing", failRatePct=30)["level"] == "Critical"
    assert sev("model.refresh-failing", failRatePct=12)["level"] == "Warning"


def test_report_too_many_visuals():
    assert sev("report.too-many-visuals", visuals=41)["level"] == "Critical"
    assert sev("report.too-many-visuals", visuals=20)["level"] == "Warning"


def test_report_directquery():
    assert sev("report.directquery")["level"] == "Warning"


def test_report_slow_visual():
    assert sev("report.slow-visual", ms=12000)["level"] == "Critical"
    assert sev("report.slow-visual", ms=5000)["level"] == "Warning"


def test_pipeline():
    assert sev("pipeline.failing", status="Failed")["level"] == "Critical"
    assert sev("pipeline.failing", status="Succeeded", failRatePct=15)["level"] == "Warning"
    assert sev("pipeline.gateway")["level"] == "Critical"


def test_lineage_blast_radius():
    assert sev("lineage.blast-radius", affectedCount=3)["level"] == "Critical"
    assert sev("lineage.blast-radius", affectedCount=0)["level"] == "Warning"


def test_security():
    assert sev("security.admin-grant")["level"] == "Critical"
    assert sev("security.external-share")["level"] == "Warning"
    assert sev("security.unusual-access", ratio=11)["level"] == "Critical"
    assert sev("security.unusual-access", ratio=6)["level"] == "Warning"


def test_cost():
    assert sev("cost.unused-report")["level"] == "Info"
    assert sev("cost.idle-capacity", avgCuPct=3)["level"] == "Warning"


def test_meta_and_unknown():
    assert sev("meta.detector-error")["level"] == "Warning"
    assert sev("nope")["level"] == "Info"


def test_config_override_downgrades_threshold():
    # raise the throttle-critical bar so a previously-Critical flag becomes Warning
    cfg = merge_config({"capacity": {"throttleCritPct": 99, "throttleCritMinutes": 99}})
    flag = {"type": "capacity.throttle", "evidence": {"peakCuPct": 95, "throttleMinutes": 40}}
    assert score_severity(flag, cfg)["level"] == "Warning"
    assert score_severity(flag)["level"] == "Critical"   # default still Critical


def test_partial_config_does_not_fail_unrelated_branch():
    # a config missing the 'security' domain must still score a capacity flag (lazy access)
    cfg = {"capacity": {"throttleCritPct": 90, "throttleCritMinutes": 30}}
    assert score_severity({"type": "capacity.throttle", "evidence": {"peakCuPct": 50, "throttleMinutes": 0}}, cfg)["level"] == "Warning"
