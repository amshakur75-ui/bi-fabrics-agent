"""Tests for the per-user concentration detector."""
from fabric_audit_agent.detectors.user_concentration import detect_user_concentration


def test_flags_user_over_threshold():
    facts = {"users": [
        {"user": "heavy@x.com", "sharePct": 42.0, "cuSeconds": 4200,
         "topItems": [{"name": "Model A", "cuSeconds": 4000}], "itemCount": 1},
        {"user": "light@x.com", "sharePct": 10.0, "cuSeconds": 1000,
         "topItems": [{"name": "Model B", "cuSeconds": 1000}], "itemCount": 1},
    ]}
    flags = detect_user_concentration(facts)
    assert [f["type"] for f in flags] == ["capacity.user-concentration"]   # only the over-threshold user
    f = flags[0]
    assert "heavy@x.com" in f["what"] and "Model A" in f["what"]
    assert f["evidence"]["sharePct"] == 42
    assert f["resource"] == "heavy@x.com"


def test_ranking_when_none_over_threshold():
    facts = {"users": [
        {"user": "a@x.com", "sharePct": 18.0, "cuSeconds": 1800, "topItems": [], "itemCount": 0},
        {"user": "b@x.com", "sharePct": 15.0, "cuSeconds": 1500, "topItems": [], "itemCount": 0},
    ]}
    flags = detect_user_concentration(facts)
    assert len(flags) == 1 and flags[0]["type"] == "capacity.user-ranking"
    assert "No single user" in flags[0]["what"]
    assert "a@x.com (18%)" in flags[0]["what"]   # top consumer still named
    assert flags[0]["evidence"]["userCount"] == 2


def test_empty():
    assert detect_user_concentration({"users": []}) == []
    assert detect_user_concentration({}) == []
