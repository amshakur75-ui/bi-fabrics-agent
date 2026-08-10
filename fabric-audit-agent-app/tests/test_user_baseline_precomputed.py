"""B2 (Design A' Phase B) — the precomputed baseline detector + its wiring into detect_all.

Covers the 3-layer fallback (personalized / estate / silent), the anomaly GATE (a multiple of
p95 plus an absolute floor — not a bare percentile lookup), the staleness guard, the batched
store read, and the detect_all wiring.
"""
from datetime import datetime, timedelta, timezone

from fabric_audit_agent.detectors import detect_all
from fabric_audit_agent.detectors.user_baseline import (
    detect_user_baseline_deviation_precomputed,
)
from fabric_audit_agent.context_user_baseline import create_user_baseline_store_memory

AS_OF = "2026-08-09T00:00:00Z"
NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)   # 12h after the fixtures' asOf


def _event(user, cu, item="Report A", op="ExecuteQuery", ts="2026-08-09T13:52:00Z"):
    return {"user": user, "cuSeconds": cu, "item": item, "operation": op, "ts": ts}


def _cfg(min_history=20, multiplier=3.0, floor=100, max_age_days=3,
         estate_multiplier=25.0):
    """The gate is ``cu > p95 * multiplier AND cu >= floor``.

    A bare ``cu > p95`` is a percentile lookup, not an anomaly test — it fires on ~5% of ALL
    events by construction, forever, on a perfectly healthy capacity. Fixtures below therefore
    use genuine multiples, never p95+1.
    """
    return {"activity": {"baselineMinHistory": min_history,
                         "baselineSpikeMultiplier": multiplier,
                         "baselineSpikeEstateMultiplier": estate_multiplier,
                         "baselineSpikeFloorCuSeconds": floor,
                         "baselineMaxAgeDays": max_age_days}}


def _personalized(user, count=25, p50=40.0, p95=100.0, as_of=AS_OF):
    return {"scope": "user", "user": user, "p50": p50, "p95": p95, "count": count,
            "min": 1.0, "max": p95 * 1.2, "asOf": as_of}


def _estate(count=200, p50=30.0, p95=80.0, as_of=AS_OF):
    return {"scope": "estate", "user": None, "p50": p50, "p95": p95, "count": count,
            "min": 0.5, "max": p95 * 1.5, "asOf": as_of}


def _run(facts, store, cfg=None, now=NOW):
    return detect_user_baseline_deviation_precomputed(
        facts, cfg or _cfg(), baseline_store=store, now=now)


# --- the anomaly gate -------------------------------------------------------------------

def test_no_store_returns_no_flags():
    """Safe default: nothing wired -> silent. Never fabricate an anomaly before we have data."""
    assert _run({"events": [_event("a@x", 5000.0)]}, None) == []


def test_layer1_fires_on_a_genuine_multiple_of_own_p95():
    store = create_user_baseline_store_memory([_personalized("a@x", p95=100.0)])
    flags = _run({"events": [_event("a@x", 500.0)]}, store)      # 5x p95, over the floor
    assert len(flags) == 1
    f = flags[0]
    assert f["type"] == "activity.user-baseline-deviation"
    assert f["evidence"]["baselineSource"] == "personalized"
    assert f["evidence"]["ratioVsP95"] == 5.0
    assert "their own baseline p95" in f["what"]


def test_layer1_silent_just_above_p95_but_below_the_multiplier():
    """THE headline fix. 110 CPU-s against a p95 of 100 is the top ~5% by definition and is
    NOT an anomaly. Under the old `cu > p95` rule this fired; it must now stay silent."""
    store = create_user_baseline_store_memory([_personalized("a@x", p95=100.0)])
    assert _run({"events": [_event("a@x", 110.0)]}, store) == []
    assert _run({"events": [_event("a@x", 299.0)]}, store) == []   # still under 3x
    assert _run({"events": [_event("a@x", 301.0)]}, store) != []   # over 3x -> fires


def test_absolute_floor_suppresses_a_tiny_baseline():
    """A user whose p95 is 0.10 CPU-s would trip on 0.31 CPU-s ("3x!") without a floor. That
    is noise, and printing it on a throttle card as '3.1x baseline' teaches people to ignore
    the card."""
    store = create_user_baseline_store_memory([_personalized("tiny@x", p95=0.10)])
    assert _run({"events": [_event("tiny@x", 0.5)]}, store) == []     # 5x but < floor
    assert _run({"events": [_event("tiny@x", 150.0)]}, store) != []   # clears both


def test_floor_is_configurable():
    store = create_user_baseline_store_memory([_personalized("a@x", p95=1.0)])
    assert _run({"events": [_event("a@x", 10.0)]}, store, _cfg(floor=5)) != []
    assert _run({"events": [_event("a@x", 10.0)]}, store, _cfg(floor=500)) == []


# --- the 3-layer fallback ---------------------------------------------------------------

def test_layer2_estate_fires_for_a_cold_start_user():
    store = create_user_baseline_store_memory([_estate(p95=80.0)])
    flags = _run({"events": [_event("brandnew@x", 3000.0)]}, store)
    assert len(flags) == 1
    assert flags[0]["evidence"]["baselineSource"] == "estate"
    assert "estate-wide p95" in flags[0]["what"]


def test_layer2_used_when_personalized_is_undertrained():
    """A 3-sample "personal" p95 is unreliable; prefer the well-sampled estate p95."""
    store = create_user_baseline_store_memory([
        _personalized("a@x", count=3, p95=10000.0),   # too few samples to trust
        _estate(count=500, p95=80.0),
    ])
    flags = _run({"events": [_event("a@x", 3000.0)]}, store, _cfg(min_history=20))
    assert len(flags) == 1
    assert flags[0]["evidence"]["baselineSource"] == "estate"


def test_estate_layer_uses_a_much_stricter_multiple_than_personalized():
    """A correctly-computed estate p95 is SMALL (single-digit CPU-s across all users), so a 3x
    gate would sit under the absolute floor and the floor alone would decide — making this layer
    a duplicate of absolute_cost's highCuSeconds=100, a bar this tenant's busy users clear
    routinely. Routine heavy users must not be named on a capacity card as the likely cause."""
    store = create_user_baseline_store_memory([_estate(p95=8.0)])
    cfg = _cfg(multiplier=3.0, estate_multiplier=25.0, floor=100)
    # 150 CPU-s is 18x the estate p95 and clears the floor — but a cold-start user with no
    # personal history needs something genuinely extreme, so 25x is the bar: stays silent.
    assert _run({"events": [_event("cold@x", 150.0)]}, store, cfg) == []
    # 500 CPU-s is 62x -> fires, and the evidence reports the multiplier ACTUALLY applied.
    flags = _run({"events": [_event("cold@x", 500.0)]}, store, cfg)
    assert len(flags) == 1
    assert flags[0]["evidence"]["spikeMultiplier"] == 25.0
    # The SAME cost against a personalized baseline of the same p95 fires at the 3x bar.
    store2 = create_user_baseline_store_memory([_personalized("warm@x", p95=8.0)])
    f2 = _run({"events": [_event("warm@x", 150.0)]}, store2, cfg)
    assert len(f2) == 1 and f2[0]["evidence"]["spikeMultiplier"] == 3.0


def test_layer3_silent_when_nothing_is_populated():
    store = create_user_baseline_store_memory()
    assert _run({"events": [_event("a@x", 99999.0)]}, store) == []


def test_ignores_events_missing_user_or_cost():
    store = create_user_baseline_store_memory([_estate(p95=80.0)])
    facts = {"events": [
        {"cuSeconds": 3000.0},                # no user -> skip
        _event("a@x", None),                  # no cost -> skip
        _event("b@x", "not a number"),        # non-numeric -> skip
        _event("c@x", float("inf")),          # non-finite -> skip
        _event("d@x", 3000.0),                # fires (estate, needs 25x)
    ]}
    flags = _run(facts, store)
    assert [f["resource"] for f in flags] == ["d@x"]


# --- staleness guard --------------------------------------------------------------------

def test_stale_personalized_baseline_falls_through_to_estate():
    """The nightly job fails QUIETLY (rowsWritten=0, no raise) and upsert never deletes, so a
    stale row would otherwise be presented to a human as 'their own baseline' indefinitely."""
    old = (NOW - timedelta(days=10)).isoformat().replace("+00:00", "Z")
    store = create_user_baseline_store_memory([
        _personalized("a@x", p95=100.0, as_of=old),
        _estate(p95=80.0),                                # fresh
    ])
    flags = _run({"events": [_event("a@x", 3000.0)]}, store)
    assert flags and flags[0]["evidence"]["baselineSource"] == "estate"


def test_stale_estate_baseline_silences_the_detector():
    old = (NOW - timedelta(days=10)).isoformat().replace("+00:00", "Z")
    store = create_user_baseline_store_memory([_estate(p95=80.0, as_of=old)])
    assert _run({"events": [_event("cold@x", 400.0)]}, store) == []


def test_unstamped_baseline_is_treated_as_fresh():
    """asOf is advisory. A row with no/unparseable stamp (hand-seeded, or written by an older
    build) must degrade to the previous behaviour, not silently disable the whole layer."""
    store = create_user_baseline_store_memory([_personalized("a@x", p95=100.0, as_of=None)])
    assert _run({"events": [_event("a@x", 400.0)]}, store) != []


def test_staleness_check_disabled_when_max_age_not_positive():
    old = (NOW - timedelta(days=400)).isoformat().replace("+00:00", "Z")
    store = create_user_baseline_store_memory([_personalized("a@x", p95=100.0, as_of=old)])
    assert _run({"events": [_event("a@x", 400.0)]}, store, _cfg(max_age_days=0)) != []


# --- batched store read -----------------------------------------------------------------

def test_uses_bulk_load_and_never_queries_per_event():
    """get_user was called once PER EVENT — thousands of Spark round-trips inside a 5-minute
    job. The detector must issue ONE bulk load regardless of event count."""
    store = create_user_baseline_store_memory([_personalized("a@x", p95=100.0)])
    calls = {"bulk": 0, "per_user": 0}
    real_bulk = store["get_all_users"]

    def counting_bulk():
        calls["bulk"] += 1
        return real_bulk()

    def counting_get_user(u):
        calls["per_user"] += 1
        return None

    store["get_all_users"] = counting_bulk
    store["get_user"] = counting_get_user
    facts = {"events": [_event("a@x", 400.0 + i) for i in range(50)]}
    flags = _run(facts, store)
    assert len(flags) == 50
    assert calls["bulk"] == 1
    assert calls["per_user"] == 0


def test_falls_back_to_per_user_lookup_when_store_lacks_bulk():
    """An older adapter / custom fake without get_all_users must still work."""
    store = create_user_baseline_store_memory([_personalized("a@x", p95=100.0)])
    del store["get_all_users"]
    assert _run({"events": [_event("a@x", 400.0)]}, store) != []


def test_bulk_load_failure_degrades_to_estate():
    store = create_user_baseline_store_memory([_personalized("a@x", p95=100.0),
                                               _estate(p95=80.0)])

    def boom():
        raise RuntimeError("delta unavailable")
    store["get_all_users"] = boom
    store["get_user"] = lambda u: (_ for _ in ()).throw(RuntimeError("also down"))
    flags = _run({"events": [_event("a@x", 3000.0)]}, store)
    assert flags and flags[0]["evidence"]["baselineSource"] == "estate"


def test_estate_failure_is_isolated_and_stays_silent():
    store = create_user_baseline_store_memory()

    def boom():
        raise RuntimeError("query timeout")
    store["get_estate"] = boom
    assert _run({"events": [_event("a@x", 400.0)]}, store) == []


# --- detect_all wiring ------------------------------------------------------------------

def test_detect_all_default_does_not_run_baseline_detector():
    """Existing call sites (`detect_all(facts, config)`) must behave EXACTLY as before."""
    facts = {"events": [_event("a@x", 5000.0)], "items": [], "capacity": {}}
    flags = detect_all(facts, {})
    assert not any(f.get("type") == "activity.user-baseline-deviation" for f in flags)


def test_detect_all_wires_baseline_store_when_provided():
    store = create_user_baseline_store_memory([_personalized("a@x", p95=100.0, as_of=None)])
    facts = {"events": [_event("a@x", 500.0)], "items": [], "capacity": {}}
    flags = detect_all(facts, _cfg(), baseline_store=store)
    got = [f for f in flags if f.get("type") == "activity.user-baseline-deviation"]
    assert len(got) == 1


def test_detect_all_isolates_a_totally_broken_store():
    bad = {"get_user": lambda u: (_ for _ in ()).throw(RuntimeError("boom")),
           "get_estate": lambda: (_ for _ in ()).throw(RuntimeError("boom")),
           "get_all_users": lambda: (_ for _ in ()).throw(RuntimeError("boom"))}
    facts = {"events": [_event("a@x", 5000.0)], "items": [], "capacity": {}}
    flags = detect_all(facts, _cfg(), baseline_store=bad)
    assert [f for f in flags if f.get("type") == "activity.user-baseline-deviation"] == []
