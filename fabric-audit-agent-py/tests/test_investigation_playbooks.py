# tests/test_investigation_playbooks.py
from fabric_audit_agent.investigation.playbooks import investigate_user
from fabric_audit_agent.adapters.reasoner_investigation import create_investigation_reasoner
from fabric_audit_agent.investigation.playbooks import investigate_capacity_spike


def _facts(users):
    return {"capacity": {"peakCuPct": 120.0, "throttleMinutes": 10},
            "items": [{"workspace": "Sales", "name": "A4A", "sharePct": 90, "attributionMode": "cost",
                       "topUsers": [{"user": "x@co", "cuSeconds": 900}], "userCount": 1}],
            "users": users}


def _collector(facts):
    return {"collect": lambda: facts}


def test_investigate_user_found_builds_grounded_result():
    facts = _facts([{"user": "x@co", "cuSeconds": 900, "sharePct": 90,
                     "topItems": [{"name": "A4A", "cuSeconds": 900}], "itemCount": 1}])
    out = investigate_user(_collector(facts), create_investigation_reasoner(), "x@co", days=30)
    assert out["abstained"] is False
    assert out["coverage"]["workspacesSeen"] == ["Sales"]
    assert any("A4A" in e["summary"] or "90" in str(e["data"]) for e in out["evidence"])
    assert "x@co" in out["result"]["explanation"]


def test_investigate_user_absent_abstains_not_hallucinates():
    facts = _facts([{"user": "someone@co", "cuSeconds": 5, "sharePct": 100, "topItems": [], "itemCount": 0}])
    out = investigate_user(_collector(facts), create_investigation_reasoner(), "ghost@co", days=30)
    assert out["abstained"] is True
    assert out["confidence"]["level"] == "insufficient"
    assert out["result"]["hypotheses"] == []     # never invents a cause for a user it can't see


def test_capacity_spike_names_top_driver_when_throttled():
    facts = _facts([{"user": "x@co", "cuSeconds": 900, "sharePct": 90, "topItems": [{"name": "A4A", "cuSeconds": 900}], "itemCount": 1}])
    out = investigate_capacity_spike(_collector(facts), create_investigation_reasoner())
    assert out["abstained"] is False
    assert any("120" in str(e["data"]) or "120" in e["summary"] for e in out["evidence"])  # peak CU%
    assert any("A4A" in e["summary"] for e in out["evidence"])                              # top item


def test_capacity_spike_abstains_without_capacity_signal():
    facts = {"items": [], "users": []}   # no capacity events wired
    out = investigate_capacity_spike(_collector(facts), create_investigation_reasoner())
    assert out["abstained"] is True and out["confidence"]["level"] == "insufficient"
