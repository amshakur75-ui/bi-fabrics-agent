"""Step 2 stateless gates: same-item cross-user (proxy) + cross-source blind-spot (meta).

Covers detection thresholds AND the alert integration (title/facts/severity/materiality/dedup key),
so a fired gate produces a well-formed, deduped alert the same way the existing gates do."""
from datetime import datetime, timezone

from fabric_audit_agent.automation.tier2_check import (
    _check_same_item_cross_user, _check_cross_source_blind_spot, _title_for, _facts_for,
    process_alerts)
from fabric_audit_agent.automation.incident import incident_key, severity_of
from fabric_audit_agent.automation.materiality import classify, load_cfg
from fabric_audit_agent.context_alerts import create_alerts_store_memory
from fabric_audit_agent.adapters.delivery_webhook import PROXY_RANKING_DISCLOSURE

T0 = datetime(2026, 8, 4, 10, 0, 0, tzinfo=timezone.utc)


def _item(cu, users):
    return {"name": "Sales", "workspace": "Fin", "cuSeconds": cu, "sharePct": 30.0,
            "topUsers": [{"user": u, "cuSeconds": c} for u, c in users], "userCount": len(users)}


def test_cross_user_fires_when_three_users_each_material():
    # 3 users each ~33% of the item -> cross-user pattern
    facts = {"items": [_item(300.0, [("a@x", 100), ("b@x", 100), ("c@x", 100)])]}
    trigs = _check_same_item_cross_user(facts)
    assert len(trigs) == 1
    t = trigs[0]
    assert t["check"] == "cross_user" and t["userCount"] == 3 and t["item"] == "Sales"


def test_cross_user_silent_when_one_user_dominates():
    # one user 90%, two tiny -> NOT cross-user (single-user concentration, a different gate)
    facts = {"items": [_item(1000.0, [("a@x", 900), ("b@x", 60), ("c@x", 40)])]}
    assert _check_same_item_cross_user(facts) == []


def test_blind_spot_fires_on_high_cu_zero_items():
    facts = {"capacity": {"peakCuPct": 82.0}, "items": []}
    trigs = _check_cross_source_blind_spot(facts)
    assert len(trigs) == 1 and trigs[0]["check"] == "blind_spot" and trigs[0]["peakCuPct"] == 82.0


def test_blind_spot_silent_when_activity_present_or_cu_low():
    assert _check_cross_source_blind_spot({"capacity": {"peakCuPct": 82.0},
                                           "items": [{"name": "x"}]}) == []
    assert _check_cross_source_blind_spot({"capacity": {"peakCuPct": 20.0}, "items": []}) == []


def test_new_gates_have_titles_facts_keys_and_severity():
    cu = {"check": "cross_user", "item": "Sales", "workspace": "Fin", "userCount": 4,
          "users": ["a", "b", "c", "d"], "sharePct": 30.0}
    bs = {"check": "blind_spot", "peakCuPct": 82.0}
    assert "Cross-user load: Sales" in _title_for(cu) and "Coverage gap" in _title_for(bs)
    assert incident_key(cu) == "cross_user::Fin/Sales" and incident_key(bs) == "blind_spot::capacity"
    assert severity_of(cu) == "warn"          # 4 users -> warn
    assert severity_of(bs) == "info"          # coverage note
    assert classify(cu)[0] == "report" and classify(bs)[0] == "report"
    assert any(n == "Distinct users" for n, _ in _facts_for(cu))


def _sink():
    posts = []
    return posts, {"deliver": lambda b: (posts.append(b), {"delivered": True, "status": 202})[1]}


def test_cross_user_alert_carries_proxy_disclosure_end_to_end():
    store = create_alerts_store_memory()
    posts, sink = _sink()
    trig = {"check": "cross_user", "item": "Sales", "workspace": "Fin", "userCount": 4,
            "users": ["a", "b", "c", "d"], "sharePct": 30.0}
    cfg = load_cfg(); cfg["hysteresis_ticks"] = 1  # isolate disclosure check from the persistence gate
    a = process_alerts([trig], now_dt=T0, alerts_store=store, delivery_sinks={"webhook": sink},
                       reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True},
                       app_url="https://app", cfg=cfg)
    assert a["new"] == ["cross_user::Fin/Sales"]
    assert PROXY_RANKING_DISCLOSURE in __import__("json").dumps(posts[-1])
