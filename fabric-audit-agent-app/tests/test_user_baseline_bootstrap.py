"""B1 (Design A' Phase B) — nightly aggregation of 14-day Log Analytics activity into per-user +
estate-wide baseline rows, plus the in-memory store shape used by B2's precomputed detector."""

from fabric_audit_agent.automation.user_baseline_bootstrap import (
    build_baselines, run_bootstrap,
)
from fabric_audit_agent.context_user_baseline import (
    create_user_baseline_store_memory, _to_row, _from_row,
)


def _events(user, values):
    return [{"user": user, "cuSeconds": v} for v in values]


def test_build_baselines_emits_one_row_per_qualifying_user():
    events = _events("a@x", list(range(1, 21))) + _events("b@x", list(range(10, 30)))
    rows = build_baselines(events, min_history=20)
    scopes = [(r["scope"], r["user"]) for r in rows]
    assert ("user", "a@x") in scopes
    assert ("user", "b@x") in scopes
    a = next(r for r in rows if r["user"] == "a@x")
    assert a["count"] == 20
    assert a["p50"] == 10.5                              # midpoint of 1..20
    assert a["min"] == 1 and a["max"] == 20
    # p95 sits at the high tail
    assert 18.0 <= a["p95"] <= 20.0


def test_build_baselines_skips_users_below_min_history():
    events = _events("a@x", [1, 2, 3]) + _events("b@x", list(range(1, 26)))
    rows = build_baselines(events, min_history=20)
    users = {r["user"] for r in rows if r["scope"] == "user"}
    assert users == {"b@x"}                              # a@x had only 3 rows, silent


def test_build_baselines_always_emits_estate_when_events_present():
    """Estate row exists even if NO individual user meets min_history — cold-start coverage
    is exactly the case where the estate baseline is what the detector will fall back to."""
    events = _events("a@x", [1, 2, 3]) + _events("b@x", [4, 5, 6])
    rows = build_baselines(events, min_history=20)
    estate = [r for r in rows if r["scope"] == "estate"]
    assert len(estate) == 1
    e = estate[0]
    assert e["user"] is None
    assert e["count"] == 6                               # 3 + 3 across both users
    assert e["min"] == 1 and e["max"] == 6


def test_build_baselines_stable_on_empty_input():
    assert build_baselines([]) == []
    assert build_baselines(None) == []


def test_build_baselines_ignores_missing_user_or_cost():
    events = [
        {"user": "a@x", "cuSeconds": 5.0},
        {"user": None, "cuSeconds": 100.0},              # skipped — no user
        {"user": "b@x", "cuSeconds": None},              # skipped — no cost
        {"user": "c@x", "cuSeconds": "not a number"},    # skipped — non-numeric
        {"user": "d@x", "cuSeconds": float("inf")},      # skipped — non-finite
        {"user": "e@x", "cuSeconds": 7.5},
    ]
    rows = build_baselines(events, min_history=1)
    users = {r["user"] for r in rows if r["scope"] == "user"}
    assert users == {"a@x", "e@x"}


def test_build_baselines_preserves_real_zero_cost():
    """A real 0.0 cuSeconds is legitimate telemetry (e.g. cache hit) — the numeric guard must
    NOT confuse it with missing data. Baseline should aggregate it faithfully."""
    events = _events("a@x", [0.0, 0.0, 0.0])
    rows = build_baselines(events, min_history=3)
    a = next(r for r in rows if r["user"] == "a@x")
    assert a["count"] == 3 and a["p50"] == 0.0 and a["max"] == 0.0


def test_build_baselines_stamps_asof_when_provided():
    rows = build_baselines(_events("a@x", [1, 2, 3]), min_history=1,
                            as_of="2026-08-09T00:00:00Z")
    assert all(r["asOf"] == "2026-08-09T00:00:00Z" for r in rows)


def test_run_bootstrap_wires_collector_and_store():
    """The orchestration: pull events via the collector, aggregate, upsert. Returns a summary
    (rowsWritten / users / hasEstate) for logging."""
    events = _events("a@x", list(range(1, 21))) + _events("b@x", list(range(1, 21)))
    collector = {"collect": lambda: events}
    store = create_user_baseline_store_memory()
    summary = run_bootstrap(collector, min_history=20, as_of="2026-08-09T00:00:00Z",
                            baseline_store=store)
    assert summary["users"] == 2                          # a@x + b@x each qualified
    assert summary["hasEstate"] is True
    assert summary["rowsWritten"] == 3                    # 2 user rows + 1 estate row
    # Store now round-trips the writes
    a = store["get_user"]("a@x")
    assert a is not None and a["scope"] == "user" and a["p95"] is not None
    e = store["get_estate"]()
    assert e is not None and e["scope"] == "estate" and e["user"] is None


def test_store_get_user_missing_returns_none():
    """Cold-start user: no personalized baseline yet. Layer-1 miss triggers layer-2 fallback
    in the detector."""
    store = create_user_baseline_store_memory()
    assert store["get_user"]("nobody@x") is None
    assert store["get_estate"]() is None                  # empty store — estate absent too


def test_store_upsert_overwrites_prior_rows():
    """A re-run overwrites the prior baseline for the same (scope, user) — this is a rebuild,
    not append. Old asOf gets replaced with the fresh timestamp."""
    store = create_user_baseline_store_memory()
    store["upsert_many"]([{"scope": "user", "user": "a@x", "p50": 5.0, "p95": 10.0,
                            "count": 20, "min": 1.0, "max": 15.0, "asOf": "d1"}])
    store["upsert_many"]([{"scope": "user", "user": "a@x", "p50": 6.0, "p95": 12.0,
                            "count": 25, "min": 2.0, "max": 18.0, "asOf": "d2"}])
    row = store["get_user"]("a@x")
    assert row["p95"] == 12.0 and row["asOf"] == "d2" and row["count"] == 25


def test_store_seeds_from_initial_rows():
    seed = [{"scope": "user", "user": "a@x", "p50": 5.0, "p95": 10.0, "count": 20,
             "min": 1.0, "max": 15.0, "asOf": "d0"},
            {"scope": "estate", "user": None, "p50": 4.0, "p95": 9.0, "count": 100,
             "min": 0.5, "max": 20.0, "asOf": "d0"}]
    store = create_user_baseline_store_memory(seed)
    assert store["get_user"]("a@x")["p95"] == 10.0
    assert store["get_estate"]()["p95"] == 9.0
    assert len(store["all"]()) == 2


def test_row_maps_are_lossless_roundtrip():
    """Delta MERGE uses ``_to_row`` on write and ``_from_row`` on read — this must be
    lossless for every column, else production writes silently drop fields."""
    original = {"scope": "user", "user": "a@x", "p50": 5.0, "p95": 10.0,
                "count": 20, "min": 1.0, "max": 15.0, "asOf": "d1"}
    assert _from_row(_to_row(original)) == original
