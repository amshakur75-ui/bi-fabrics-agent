"""Source registry + coverage resolver (spec: capability model). Offline, env-injected."""
from fabric_audit_agent.sources import SOURCES, resolve_sources

_FULL_T2 = {
    "FABRIC_CAPACITY_EVENTS_CLUSTER": "https://x.kusto.fabric.microsoft.com",
    "FABRIC_CAPACITY_EVENTS_DB": "db",
    "FABRIC_CLIENT_ID": "cid", "FABRIC_TENANT_ID": "t", "FABRIC_CLIENT_SECRET": "s",
    "FABRIC_LA_WORKSPACE_ID": "ws",
}

def test_registry_declares_all_six_sources_with_descriptors():
    for sid in ("csv", "capacity_events", "activity", "fuam", "events_la", "workspace_monitoring"):
        assert sid in SOURCES
        d = SOURCES[sid]["descriptor"]
        assert set(d) >= {"provides", "liveness", "authority", "scope"}

def test_full_tier2_env_gives_eventdepth_from_la():
    cov = resolve_sources(_FULL_T2)["coverage"]
    assert cov["byCapability"]["eventDepth"]["source"] == "events_la"
    assert cov["byCapability"]["capacityCU"]["source"] == "capacity_events"
    assert cov["blind"] == []
    # perItemCU here is served by events_la, a PROXY-authority source (no FUAM configured) --
    # degraded now flags this consciously (Task-1 follow-up: proxy-perItemCU is noted, not just csv).
    assert any("per-item CU is a proxy or estimate" in n for n in cov["degraded"])

def test_workspace_monitoring_only_env_covers_per_item_cu_as_proxy():
    env = {"FABRIC_KUSTO_CLUSTER": "c", "FABRIC_KUSTO_DB": "db", "FABRIC_CLIENT_ID": "cid"}
    cov = resolve_sources(env)["coverage"]
    assert cov["byCapability"]["perItemCU"] == {
        "source": "workspace_monitoring", "liveness": "live", "authority": "proxy",
    }
    assert any("per-item CU is a proxy or estimate" in n for n in cov["degraded"])

def test_tier1_only_env_degrades_eventdepth_not_blind_on_attribution():
    env = {"FABRIC_CLIENT_ID": "cid", "FABRIC_TENANT_ID": "t", "FABRIC_CLIENT_SECRET": "s"}
    cov = resolve_sources(env)["coverage"]
    assert cov["byCapability"]["userAttribution"]["source"] == "activity"
    assert cov["byCapability"]["eventDepth"] is None
    assert "eventDepth" in cov["blind"]
    assert any("per-query" in n for n in cov["degraded"])

def test_empty_env_everything_blind():
    cov = resolve_sources({})["coverage"]
    assert cov["byCapability"]["capacityCU"] is None
    assert set(cov["blind"]) == {"capacityCU", "userAttribution", "perItemCU", "eventDepth", "owner"}

def test_authority_beats_liveness_csv_vs_capacity_events():
    # capacity_events (live, authoritative) beats csv (offline, authoritative) on liveness tiebreak.
    env = {**_FULL_T2, "FABRIC_CSV_PATHS": "a.csv"}
    cov = resolve_sources(env)["coverage"]
    assert cov["byCapability"]["capacityCU"]["source"] == "capacity_events"

def test_zero_string_env_value_is_unconfigured_but_present_key_with_value_counts():
    # env gates are "non-empty string present" — a real value "0" IS configured (nullish discipline).
    env = {"FABRIC_CSV_PATHS": "0"}
    cov = resolve_sources(env)["coverage"]
    assert cov["byCapability"]["perItemCU"]["source"] == "csv"
