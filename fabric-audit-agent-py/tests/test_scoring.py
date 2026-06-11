from fabric_audit_agent.key_utils import domain_of
from fabric_audit_agent.health_score import build_health_score
from fabric_audit_agent.roadmap import build_roadmap
from fabric_audit_agent.verdict import build_capacity_verdict


def _f(key, level, what="w", fix=None, recurring=None):
    d = {"key": key, "what": what, "score": {"level": level, "reason": "r"}}
    if fix is not None:
        d["fix"] = fix
    if recurring is not None:
        d["recurringRuns"] = recurring
    return d


# ---- domain_of ----
def test_domain_of():
    assert domain_of("capacity.throttle::X / Y") == "capacity"
    assert domain_of("model.bidirectional::Z") == "model"
    assert domain_of("weird") == "other"
    assert domain_of(None) == "other"


# ---- health score ----
def test_health_score_overall_and_by_domain():
    findings = [_f("capacity.throttle::a", "Critical"), _f("capacity.contention::b", "Warning"), _f("model.bidirectional::c", "Warning")]
    hs = build_health_score(findings)
    assert hs["overall"] == 100 - (8 + 3 + 3)          # 86
    assert hs["byDomain"]["capacity"] == 100 - (8 + 3)  # 89
    assert hs["byDomain"]["model"] == 100 - 3           # 97


def test_health_score_floors_at_zero_and_empty():
    many = [_f(f"capacity.throttle::{i}", "Critical") for i in range(20)]
    assert build_health_score(many)["overall"] == 0
    assert build_health_score([]) == {"overall": 100, "byDomain": {}}


# ---- roadmap ----
def test_roadmap_orders_by_severity_then_recurring():
    findings = [_f("a", "Warning", recurring=1), _f("b", "Critical", recurring=1), _f("c", "Warning", recurring=5), _f("d", "Info")]
    ranked = build_roadmap(findings)
    assert [r["key"] for r in ranked] == ["b", "c", "a", "d"]   # Critical, Warning(5), Warning(1), Info
    assert ranked[0]["rank"] == 1 and ranked[1]["recurringRuns"] == 5


def test_roadmap_fix_first_element_or_none():
    assert build_roadmap([_f("a", "Warning", fix=["do X", "do Y"])])[0]["fix"] == "do X"
    assert build_roadmap([_f("a", "Warning")])[0]["fix"] is None


# ---- verdict ----
def test_verdict_unknown_without_capacity():
    assert build_capacity_verdict({}, [])["decision"] == "unknown"


def test_verdict_healthy_when_no_throttle():
    facts = {"capacity": {"capacityId": "F64", "peakCuPct": 50, "sku": "F64"}}
    assert build_capacity_verdict(facts, [])["decision"] == "healthy"


def test_verdict_optimize_when_throttle_plus_optimizations():
    facts = {"capacity": {"capacityId": "F64", "peakCuPct": 95, "throttleMinutes": 20, "sku": "F64"}}
    flags = [{"type": "capacity.throttle"}, {"type": "capacity.contention"}, {"type": "capacity.oversized-model"}]
    v = build_capacity_verdict(facts, flags)
    assert v["decision"] == "optimize"
    assert v["evidence"]["optimizations"] == ["capacity.contention", "capacity.oversized-model"]


def test_verdict_size_up_when_throttle_no_optimizations():
    facts = {"capacity": {"capacityId": "F64", "peakCuPct": 95, "throttleMinutes": 20, "sku": "F64"}}
    v = build_capacity_verdict(facts, [{"type": "capacity.throttle"}])
    assert v["decision"] == "size-up" and v["evidence"]["recommendedSku"] == "F128"


def test_verdict_size_up_unknown_sku_fallback():
    facts = {"capacity": {"capacityId": "X", "peakCuPct": 95, "throttleMinutes": 20, "sku": "F512"}}
    assert build_capacity_verdict(facts, [{"type": "capacity.throttle"}])["evidence"]["recommendedSku"] == "next tier up"
