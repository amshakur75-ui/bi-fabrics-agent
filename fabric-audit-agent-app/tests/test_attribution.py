from fabric_audit_agent.attribution import attribute_users, enrich_items


def test_cost_mode_ranks_by_cost_top3_plus_count():
    events = [
        {"user": "a@x.com", "cpuMs": 5000, "interactive": True},
        {"user": "b@x.com", "cpuMs": 9000, "interactive": True},
        {"user": "c@x.com", "durationMs": 1000, "interactive": True},
        {"user": "d@x.com", "cpuMs": 100, "interactive": True},
        {"user": "b@x.com", "cpuMs": 3000, "interactive": True},
    ]
    r = attribute_users(events, top_n=3)
    assert r["mode"] == "cost"
    assert r["userCount"] == 4
    assert r["topUsers"][0]["user"] == "b@x.com"   # 12000 = highest summed cost
    assert len(r["topUsers"]) == 3
    assert r["background"] is False


def test_frequency_mode_ranks_by_op_count():
    r = attribute_users([
        {"user": "a@x.com", "interactive": True},
        {"user": "a@x.com", "interactive": True},
        {"user": "b@x.com", "interactive": True},
    ])
    assert r["mode"] == "frequency"
    assert r["topUsers"][0]["user"] == "a@x.com"
    assert r["topUsers"][0]["ops"] == 2


def test_flags_background_dominated_and_carries_owner():
    r = attribute_users([
        {"user": "svc@x.com", "interactive": False, "cpuMs": 100000},
        {"user": "svc@x.com", "interactive": False, "cpuMs": 50000},
        {"user": "viewer@x.com", "interactive": True, "cpuMs": 10},
    ], owner="owner@x.com")
    assert r["background"] is True
    assert r["owner"] == "owner@x.com"


def test_cost_weighted_background_outweighs_many_cheap_interactive():
    r = attribute_users([
        {"user": "svc@x.com", "interactive": False, "cpuMs": 500000},
        {"user": "v1@x.com", "interactive": True, "cpuMs": 50},
        {"user": "v2@x.com", "interactive": True, "cpuMs": 50},
        {"user": "v3@x.com", "interactive": True, "cpuMs": 50},
    ])
    assert r["background"] is True   # 3 of 4 ops interactive, but background dominates COST


def test_enrich_items_attaches_attribution_leaves_others_untouched():
    items = [{"name": "GL Model", "workspace": "Fin", "sharePct": 70}, {"name": "Other", "sharePct": 5}]
    events_by_item = {"GL Model": [
        {"user": "a@x.com", "cpuMs": 10, "interactive": True},
        {"user": "b@x.com", "cpuMs": 5, "interactive": True},
    ]}
    out = enrich_items(items, events_by_item)
    gl = next(i for i in out if i["name"] == "GL Model")
    assert gl["userCount"] == 2
    assert gl["topUsers"][0]["user"] == "a@x.com"
    assert gl["attributionMode"] == "cost"
    assert "topUsers" not in next(i for i in out if i["name"] == "Other")


def test_ignores_events_with_no_user():
    r = attribute_users([{"cpuMs": 100, "interactive": True}, {"user": "", "interactive": True}])
    assert r["userCount"] == 0
    assert r["topUsers"] == []


def test_non_finite_cost_does_not_crash():
    # NaN/Infinity must behave like JS Number.isFinite (ignored), not crash round()
    r = attribute_users([{"user": "a@x.com", "cpuMs": float("inf"), "interactive": True}])
    assert r["userCount"] == 1
    assert r["mode"] == "frequency"          # inf is not finite -> not treated as a cost
    assert r["topUsers"][0]["cpuMs"] == 0    # non-finite cost coerced to 0


def test_enrich_owner_nullish_keeps_empty_string():
    out = enrich_items(
        [{"name": "X", "sharePct": 50, "owner": ""}],
        {"X": [{"user": "u@x.com", "cpuMs": 1, "interactive": True}]},
        owner="fallback@x.com",
    )
    assert out[0]["owner"] == ""             # present-but-empty owner kept (nullish, not falsy)


def test_cpuMs_rounds_half_up_like_js():
    r = attribute_users([{"user": "a@x.com", "cpuMs": 2.5, "interactive": True}])
    assert r["topUsers"][0]["cpuMs"] == 3    # JS Math.round(2.5)=3 (not banker's-rounding 2)
