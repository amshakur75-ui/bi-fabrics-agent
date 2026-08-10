"""Server-side baseline aggregation + the Tier-2 events wiring that made B2/B3 reachable.

Two distinct regressions are pinned here:

1. The nightly bootstrap used to reuse the SWEEP event collector, which caps at the 5,000
   COSTLIEST rows. Over 14 days that keeps ~1% of events, so the computed "p95" was really
   ~p99.9 (measured live: estate p95 = 1052 CPU-s). Percentiles are now computed in KQL with
   no cap.
2. `facts["events"]` did not exist in the Tier-2 sweep at all — `_build_tier2_collector`
   composed only capacity-events + LA-attribution, so the baseline detector and correlation
   booster were DEAD CODE in production. Every test injected the key by hand, which is exactly
   why nothing caught it.
"""
from unittest.mock import patch

from fabric_audit_agent.adapters.collector_baseline_la import (
    build_per_user_kql, build_estate_kql, create_baseline_collector,
)
from fabric_audit_agent.automation.user_baseline_bootstrap import run_bootstrap_aggregate
from fabric_audit_agent.context_user_baseline import create_user_baseline_store_memory
from fabric_audit_agent.job import _build_tier2_collector

WINDOW = "| where TimeGenerated > ago(14d)"


# --- KQL shape --------------------------------------------------------------------------

def test_per_user_kql_aggregates_server_side_with_no_row_cap():
    k = build_per_user_kql(WINDOW)
    assert "summarize" in k
    assert "percentile(_cu, 95)" in k and "percentile(_cu, 50)" in k
    assert "by _euser" in k
    # THE bug: a `top N by cost` cap is what biased p95 into p99.9. It must not appear.
    assert "top " not in k
    assert "by coalesce(CpuTimeMs, DurationMs) desc" not in k


def test_estate_kql_is_its_own_aggregate_not_a_mean_of_percentiles():
    k = build_estate_kql(WINDOW)
    assert "summarize" in k and "percentile(_cu, 95)" in k
    # No `by` clause -> one row across all users. An average of per-user percentiles is NOT a
    # percentile, so the estate row must be computed independently.
    assert "by _euser" not in k
    assert "top " not in k


def test_kql_uses_the_same_cost_definition_as_normalize_event():
    """Baseline p95 and live event cuSeconds must be the same unit or the threshold is
    meaningless. normalize_event does coalesce(CpuTimeMs, DurationMs)/1000."""
    for k in (build_per_user_kql(WINDOW), build_estate_kql(WINDOW)):
        assert "coalesce(CpuTimeMs, DurationMs)) / 1000.0" in k


def test_kql_excludes_vertipaq_storage_engine_children_by_default():
    """VertiPaqSE* events are CHILDREN of a QueryEnd; counting them double-counts cost and
    pollutes the distribution with raw scans."""
    assert 'not(OperationName startswith "VertiPaqSE")' in build_per_user_kql(WINDOW)


def test_kql_resolves_user_from_either_column():
    """XMLA read sessions leave ExecutingUser empty and carry the caller in EffectiveUsername."""
    k = build_per_user_kql(WINDOW)
    assert "ExecutingUser" in k and "EffectiveUsername" in k


def test_window_clause_is_spliced_verbatim():
    assert "ago(3d)" in build_per_user_kql("| where TimeGenerated > ago(3d)")


# --- row mapping ------------------------------------------------------------------------

def _fake_query(user_rows, estate_rows):
    calls = []

    def q(kql):
        calls.append(kql)
        return estate_rows if "by _euser" not in kql else user_rows
    return q, calls


def test_collect_maps_rows_into_baseline_shape():
    users = [{"user": "a@x", "p50": 40.0, "p95": 100.0, "sampleCount": 250,
              "minCu": 1.0, "maxCu": 900.0}]
    estate = [{"p50": 30.0, "p95": 80.0, "sampleCount": 90000, "minCu": 0.1, "maxCu": 5000.0}]
    q, _ = _fake_query(users, estate)
    rows = create_baseline_collector(q, {"window": WINDOW, "minHistory": 20,
                                          "asOf": "2026-08-10T02:00:00Z"})["collect"]()
    u = next(r for r in rows if r["scope"] == "user")
    assert u == {"scope": "user", "user": "a@x", "p50": 40.0, "p95": 100.0, "count": 250,
                 "min": 1.0, "max": 900.0, "asOf": "2026-08-10T02:00:00Z"}
    e = next(r for r in rows if r["scope"] == "estate")
    assert e["user"] is None and e["count"] == 90000 and e["p95"] == 80.0


def test_collect_drops_undertrained_users_but_always_keeps_estate():
    users = [{"user": "few@x", "p50": 1.0, "p95": 2.0, "sampleCount": 3,
              "minCu": 1.0, "maxCu": 3.0}]
    estate = [{"p50": 30.0, "p95": 80.0, "sampleCount": 3, "minCu": 0.1, "maxCu": 99.0}]
    q, _ = _fake_query(users, estate)
    rows = create_baseline_collector(q, {"minHistory": 20})["collect"]()
    assert [r["scope"] for r in rows] == ["estate"]      # cold-start coverage never gaps


def test_collect_skips_rows_with_no_usable_p95_or_user():
    users = [{"user": "", "p95": 10.0, "sampleCount": 100},
             {"user": "b@x", "p95": None, "sampleCount": 100}]
    q, _ = _fake_query(users, [])
    assert create_baseline_collector(q, {"minHistory": 1})["collect"]() == []


def test_collect_stable_on_empty_source():
    q, _ = _fake_query([], [])
    assert create_baseline_collector(q)["collect"]() == []


def test_collect_issues_exactly_two_queries():
    q, calls = _fake_query([], [])
    create_baseline_collector(q)["collect"]()
    assert len(calls) == 2       # per-user + estate; NOT one per user


# --- run_bootstrap_aggregate ------------------------------------------------------------

def test_run_bootstrap_aggregate_upserts_and_restamps_as_of():
    rows = [{"scope": "user", "user": "a@x", "p50": 1.0, "p95": 2.0, "count": 30,
             "min": 0.1, "max": 9.0, "asOf": "stale"},
            {"scope": "estate", "user": None, "p50": 1.0, "p95": 2.0, "count": 500,
             "min": 0.1, "max": 9.0, "asOf": "stale"}]
    store = create_user_baseline_store_memory()
    summary = run_bootstrap_aggregate({"collect": lambda: rows},
                                       as_of="2026-08-10T02:00:00Z", baseline_store=store)
    assert summary == {"rowsWritten": 2, "users": 1, "hasEstate": True,
                       "asOf": "2026-08-10T02:00:00Z"}
    assert store["get_user"]("a@x")["asOf"] == "2026-08-10T02:00:00Z"
    assert store["get_estate"]()["asOf"] == "2026-08-10T02:00:00Z"


def test_run_bootstrap_aggregate_does_not_clobber_on_empty():
    """An empty collect must NOT wipe the existing baseline — the old one stays live until a
    successful run replaces it."""
    store = create_user_baseline_store_memory([
        {"scope": "user", "user": "a@x", "p50": 1.0, "p95": 2.0, "count": 30,
         "min": 0.1, "max": 9.0, "asOf": "yesterday"}])
    summary = run_bootstrap_aggregate({"collect": lambda: []}, as_of="now",
                                       baseline_store=store)
    assert summary["rowsWritten"] == 0
    assert store["get_user"]("a@x")["asOf"] == "yesterday"


# --- THE wiring regression --------------------------------------------------------------

_ENV = {
    "FABRIC_CAPACITY_EVENTS_CLUSTER": "https://cluster.kusto.windows.net",
    "FABRIC_CAPACITY_EVENTS_DB": "db",
    "FABRIC_CLIENT_ID": "cid",
    "FABRIC_CLIENT_SECRET": "secret",
    "FABRIC_TENANT_ID": "tid",
    "FABRIC_LA_WORKSPACE_ID": "la-ws",
    # The raw-events pull is gated on the same flag as the detectors it feeds.
    "TIER2_BASELINE_ENABLED": "1",
}


def test_tier2_collector_now_includes_a_raw_events_source():
    """B2/B3 read facts["events"]. The attribution collector SUMMARIZES and emits items/users
    only, so without a raw event source the key never exists and both features are dead code."""
    seen = {}

    def fake_events(env, window=None):
        seen["window"] = window
        return {"collect": lambda: {"events": [{"user": "a@x", "cuSeconds": 1.0}]}}

    with patch("fabric_audit_agent.job._build_events_collector", side_effect=fake_events), \
         patch("fabric_audit_agent.adapters.clients.build_kusto_query"), \
         patch("fabric_audit_agent.adapters.clients.build_log_analytics_query"):
        collector = _build_tier2_collector(dict(_ENV), window="5m")
        facts = collector["collect"]()

    assert "events" in facts, "facts['events'] missing — B2/B3 would be dead code again"
    assert facts["events"], "events list came back empty"


def test_tier2_collector_events_pull_is_gated_on_the_baseline_flag():
    """It is a THIRD Log Analytics query on a job that runs 288x/day. Paying for it while
    TIER2_BASELINE_ENABLED is unset buys nothing — the only consumers (baseline detector +
    correlation booster) are switched off."""
    called = {"n": 0}

    def fake_events(env, window=None):
        called["n"] += 1
        return {"collect": lambda: {"events": []}}

    env_off = {k: v for k, v in _ENV.items() if k != "TIER2_BASELINE_ENABLED"}
    with patch("fabric_audit_agent.job._build_events_collector", side_effect=fake_events), \
         patch("fabric_audit_agent.adapters.clients.build_kusto_query"), \
         patch("fabric_audit_agent.adapters.clients.build_log_analytics_query"):
        _build_tier2_collector(env_off, window="5m")
    assert called["n"] == 0, "events collector must not run while the baseline flag is off"


def test_tier2_events_window_overlaps_the_sweep_cadence():
    """A 5m window leaves a PERMANENT BLIND HOLE: Power BI diagnostic logs arrive with minutes
    of ingestion latency while the KQL filters on TimeGenerated (EVENT time). A query at
    TimeGenerated=13:52 that becomes queryable at 13:56 is missed by the 13:55 sweep (not
    ingested) AND the 14:00 sweep (13:52 is outside ago(5m)) — and those are exactly the events
    next to the capacity peak. 15m overlaps; dedup handles it, matching watch_run.py."""
    seen = {}

    def fake_events(env, window=None):
        seen["window"] = window
        return {"collect": lambda: {"events": []}}

    with patch("fabric_audit_agent.job._build_events_collector", side_effect=fake_events), \
         patch("fabric_audit_agent.adapters.clients.build_kusto_query"), \
         patch("fabric_audit_agent.adapters.clients.build_log_analytics_query"):
        _build_tier2_collector(dict(_ENV), window="5m")
    assert seen["window"] == "15m", "must be WIDER than the 5m cadence, not equal to it"

    # Overridable for tuning without a redeploy.
    env2 = dict(_ENV, FABRIC_TIER2_EVENTS_WINDOW="30m")
    with patch("fabric_audit_agent.job._build_events_collector", side_effect=fake_events), \
         patch("fabric_audit_agent.adapters.clients.build_kusto_query"), \
         patch("fabric_audit_agent.adapters.clients.build_log_analytics_query"):
        _build_tier2_collector(env2, window="5m")
    assert seen["window"] == "30m"


def test_baseline_kql_lowercases_the_user_id():
    """normalize_event lowercases the live event's user. Without tolower() here the baseline row
    keys on "Abdishakur.Mohamed@..." while the event carries "abdishakur.mohamed@...", so
    per_user.get(user) misses and EVERY mixed-case user is silently demoted to the estate
    baseline — while the card still claims their personalized baseline "isn't ready yet"."""
    assert "tolower(_euser)" in build_per_user_kql(WINDOW)
    q, _ = _fake_query([{"user": "Mixed.Case@Example.COM", "p50": 1.0, "p95": 100.0,
                         "sampleCount": 99, "minCu": 0.1, "maxCu": 900.0}], [])
    rows = create_baseline_collector(q, {"minHistory": 20})["collect"]()
    assert rows[0]["user"] == "mixed.case@example.com"


def test_tier2_collector_survives_an_events_source_failure():
    """An LA outage must degrade the correlation booster, not break the capacity gates."""
    def boom(env, window=None):
        raise RuntimeError("log analytics unreachable")

    with patch("fabric_audit_agent.job._build_events_collector", side_effect=boom), \
         patch("fabric_audit_agent.adapters.clients.build_kusto_query"), \
         patch("fabric_audit_agent.adapters.clients.build_log_analytics_query"):
        collector = _build_tier2_collector(dict(_ENV), window="5m")
    assert collector is not None and "collect" in collector
