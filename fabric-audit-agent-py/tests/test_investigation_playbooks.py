# tests/test_investigation_playbooks.py
from fabric_audit_agent.investigation.playbooks import investigate_user
from fabric_audit_agent.adapters.reasoner_investigation import create_investigation_reasoner


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
