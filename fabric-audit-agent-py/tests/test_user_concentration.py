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
    assert "monitored CU" in f["what"]            # no capacity CU% wired -> share of monitored CU
    assert f["evidence"]["sharePct"] == 42
    assert f["evidence"]["estimated"] is False
    assert f["resource"] == "heavy@x.com"


def test_estimates_capacity_share_when_capacity_pct_present():
    facts = {
        "capacity": {"peakCuPct": 80.0},
        "users": [
            {"user": "heavy@x.com", "sharePct": 50.0, "cuSeconds": 5000,
             "topItems": [{"name": "Model A"}], "itemCount": 1},
            {"user": "mid@x.com", "sharePct": 30.0, "cuSeconds": 3000,
             "topItems": [{"name": "Model B"}], "itemCount": 1},
        ],
    }
    conc = [f for f in detect_user_concentration(facts) if f["type"] == "capacity.user-concentration"]
    # heavy: 50% of CPU x 80% capacity = 40% of capacity -> over 30 -> flagged
    # mid:   30% x 80% = 24% -> under 30 -> not flagged
    assert len(conc) == 1 and conc[0]["resource"] == "heavy@x.com"
    f = conc[0]
    assert f["evidence"]["sharePct"] == 40            # 50 * 80/100
    assert f["evidence"]["estimated"] is True
    assert f["evidence"]["capacityPeakPct"] == 80.0
    assert "capacity CU (est.)" in f["what"]


def test_ranking_when_none_over_threshold():
    facts = {"users": [
        {"user": "a@x.com", "sharePct": 18.0, "cuSeconds": 1800, "topItems": [], "itemCount": 0},
        {"user": "b@x.com", "sharePct": 15.0, "cuSeconds": 1500, "topItems": [], "itemCount": 0},
    ]}
    flags = detect_user_concentration(facts)
    assert len(flags) == 1 and flags[0]["type"] == "capacity.user-ranking"
    assert "No single user" in flags[0]["what"]
    assert "a@x.com (~18%)" in flags[0]["what"]   # top consumer still named
    assert flags[0]["evidence"]["userCount"] == 2


def test_empty():
    assert detect_user_concentration({"users": []}) == []
    assert detect_user_concentration({}) == []


def test_user_concentration_labels_estimate_and_keeps_monitored_share():
    facts = {"capacity": {"peakCuPct": 60.0},
             "users": [{"user": "x@co", "sharePct": 80, "cuSeconds": 800,
                        "topItems": [{"name": "A4A"}], "itemCount": 1}]}
    flags = detect_user_concentration(facts)
    f = next(f for f in flags if f["type"] == "capacity.user-concentration")
    assert f["evidence"]["estimated"] is True
    assert f["evidence"]["monitoredSharePct"] == 80          # raw monitored share preserved
    assert "est" in f["what"].lower()                         # the headline marks it an estimate
