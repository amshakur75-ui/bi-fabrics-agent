"""B2 (Design A' Phase B) — the precomputed baseline detector + its wiring into detect_all.

Covers the 3-layer fallback (personalized / estate / silent), the ``baselineSource`` evidence
that tells the reader which layer fired, and the detect_all wiring (safe no-op when the store
is absent, failure-isolated when the store raises).
"""
from fabric_audit_agent.detectors import detect_all
from fabric_audit_agent.detectors.user_baseline import (
    detect_user_baseline_deviation_precomputed,
)
from fabric_audit_agent.context_user_baseline import create_user_baseline_store_memory


def _event(user, cu, item="Report A", op="ExecuteQuery", ts="2026-08-09T13:52:00Z"):
    return {"user": user, "cuSeconds": cu, "item": item, "operation": op, "ts": ts}


def _cfg(min_history=20):
    return {"activity": {"baselineMinHistory": min_history}}


def _personalized_baseline(user, count=25, p50=5.0, p95=10.0):
    return {"scope": "user", "user": user, "p50": p50, "p95": p95, "count": count,
            "min": 1.0, "max": p95 * 1.2, "asOf": "2026-08-09T00:00:00Z"}


def _estate_baseline(count=200, p50=4.0, p95=8.0):
    return {"scope": "estate", "user": None, "p50": p50, "p95": p95, "count": count,
            "min": 0.5, "max": p95 * 1.5, "asOf": "2026-08-09T00:00:00Z"}


def test_precomputed_no_store_returns_no_flags():
    """The safe default: nothing wired -> silent. Never fabricate an anomaly before we
    have data."""
    facts = {"events": [_event("a@x", 100.0)]}
    assert detect_user_baseline_deviation_precomputed(facts, _cfg(),
                                                        baseline_store=None) == []


def test_layer1_personalized_fires_over_user_p95():
    """The user has a real baseline; today's cost is above their own p95 -> flag."""
    store = create_user_baseline_store_memory([_personalized_baseline("a@x", p95=10.0)])
    facts = {"events": [_event("a@x", 25.0)]}                   # 25s >> user p95=10s
    flags = detect_user_baseline_deviation_precomputed(facts, _cfg(), baseline_store=store)
    assert len(flags) == 1
    f = flags[0]
    assert f["type"] == "activity.user-baseline-deviation"
    assert f["evidence"]["baselineSource"] == "personalized"
    assert f["evidence"]["baselineP95"] == 10.0
    assert "their own baseline p95" in f["what"]


def test_layer1_stays_silent_when_at_or_below_personal_p95():
    """A cost at or below the user's own p95 is normal for them — no flag."""
    store = create_user_baseline_store_memory([_personalized_baseline("a@x", p95=10.0)])
    facts = {"events": [_event("a@x", 9.5), _event("a@x", 10.0)]}
    assert detect_user_baseline_deviation_precomputed(facts, _cfg(), baseline_store=store) == []


def test_layer2_estate_fires_when_personalized_missing():
    """Cold-start user with no personalized row yet — the estate baseline fills in. The
    resulting flag names the estate layer so the reader knows why."""
    store = create_user_baseline_store_memory([_estate_baseline(p95=8.0)])
    facts = {"events": [_event("brandnew@x", 20.0)]}
    flags = detect_user_baseline_deviation_precomputed(facts, _cfg(), baseline_store=store)
    assert len(flags) == 1
    assert flags[0]["evidence"]["baselineSource"] == "estate"
    assert "estate-wide p95" in flags[0]["what"]


def test_layer2_estate_used_when_personalized_undertrained():
    """A user whose personalized row exists but sample_count is BELOW min_history should
    fall through to the estate baseline — a 3-sample "personal" p95 is unreliable, we
    prefer the well-sampled estate p95."""
    store = create_user_baseline_store_memory([
        _personalized_baseline("a@x", count=3, p95=100.0),      # too few samples
        _estate_baseline(count=500, p95=8.0),
    ])
    facts = {"events": [_event("a@x", 15.0)]}
    flags = detect_user_baseline_deviation_precomputed(facts, _cfg(min_history=20),
                                                        baseline_store=store)
    assert len(flags) == 1
    assert flags[0]["evidence"]["baselineSource"] == "estate"   # fell through to L2


def test_layer3_silent_when_no_baseline_available():
    """Neither personalized nor estate is populated (bootstrap job hasn't run, or an empty
    tenant). Detector stays silent — no false alerts before we have data."""
    empty_store = create_user_baseline_store_memory()
    facts = {"events": [_event("a@x", 500.0)]}                  # huge cost, no baseline
    assert detect_user_baseline_deviation_precomputed(facts, _cfg(),
                                                        baseline_store=empty_store) == []


def test_ignores_events_missing_user_or_cost():
    store = create_user_baseline_store_memory([_estate_baseline(p95=8.0)])
    facts = {"events": [
        {"cuSeconds": 25.0},                                    # no user -> skip
        _event("a@x", None),                                    # no cost -> skip
        _event("b@x", "not a number"),                          # non-numeric -> skip
        _event("c@x", 20.0),                                    # this one fires (estate)
    ]}
    flags = detect_user_baseline_deviation_precomputed(facts, _cfg(), baseline_store=store)
    assert len(flags) == 1 and flags[0]["resource"] == "c@x"


def test_store_error_on_get_user_is_isolated_and_falls_back_to_estate():
    """A store that raises on ``get_user`` must not crash the detector — the detector
    treats it as "personalized unknown" and falls through to the estate layer."""
    store = create_user_baseline_store_memory([_estate_baseline(p95=8.0)])

    def raising_get_user(user):
        raise RuntimeError("connection reset")
    store["get_user"] = raising_get_user
    facts = {"events": [_event("a@x", 20.0)]}
    flags = detect_user_baseline_deviation_precomputed(facts, _cfg(), baseline_store=store)
    assert len(flags) == 1 and flags[0]["evidence"]["baselineSource"] == "estate"


def test_store_error_on_get_estate_is_isolated_and_stays_silent():
    """A store that raises on ``get_estate`` (no personalized row + estate unreachable)
    means the detector has no baseline to compare against — stay silent, don't crash."""
    store = create_user_baseline_store_memory()

    def raising_get_estate():
        raise RuntimeError("query timeout")
    store["get_estate"] = raising_get_estate
    facts = {"events": [_event("a@x", 20.0)]}
    assert detect_user_baseline_deviation_precomputed(facts, _cfg(),
                                                        baseline_store=store) == []


def test_detect_all_default_does_not_run_baseline_detector():
    """Existing call sites of ``detect_all(facts, config)`` with no baseline_store must
    continue to behave EXACTLY as before — the new detector is opt-in via the kwarg.
    Backwards-compat guarantee: no surprise new flag types firing on older callers."""
    facts = {"events": [_event("a@x", 500.0)], "items": [], "capacity": {}}
    flags = detect_all(facts, {})
    assert not any(f.get("type") == "activity.user-baseline-deviation" for f in flags)


def test_detect_all_wires_baseline_store_when_provided():
    """With a store threaded in, detect_all now emits baseline flags alongside the
    existing detector output. Threaded from job.run_job at deploy time."""
    store = create_user_baseline_store_memory([_personalized_baseline("a@x", p95=10.0)])
    facts = {"events": [_event("a@x", 25.0)], "items": [], "capacity": {}}
    flags = detect_all(facts, {}, baseline_store=store)
    baseline_flags = [f for f in flags if f.get("type") == "activity.user-baseline-deviation"]
    assert len(baseline_flags) == 1


def test_detect_all_isolates_baseline_detector_failure():
    """A store that raises on EVERY call (both get_user + get_estate) — the sweep must
    continue and emit the standard meta.detector-error shape, not crash."""
    bad_store = {"get_user": lambda u: (_ for _ in ()).throw(RuntimeError("boom")),
                 "get_estate": lambda: (_ for _ in ()).throw(RuntimeError("boom"))}
    facts = {"events": [_event("a@x", 100.0)], "items": [], "capacity": {}}
    # The detector itself catches per-call exceptions and falls through gracefully
    # (returning []), so no meta.detector-error flag is expected here — this test pins
    # that graceful shape. If future changes make the detector propagate, the outer
    # detect_all wrapper will still catch and emit meta.detector-error.
    flags = detect_all(facts, {}, baseline_store=bad_store)
    baseline_flags = [f for f in flags if f.get("type") == "activity.user-baseline-deviation"]
    assert baseline_flags == []
