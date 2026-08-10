"""B5 pre-deploy heavy integration — Design A' Phase B wire-up through job.py.

Verifies:
  1. TIER2_BASELINE_ENABLED gate — off by default, on flips it live, missing catalog
     silently skips the wire (no crash).
  2. TIER2_REPORTING_ENABLED gate — on by default; opt-out with "0"; missing catalog skips.
  3. run_baseline_bootstrap_job pure DI path — memory collector + memory store, verifies
     the row set matches the expected shape and asOf is stamped.
  4. run_baseline_bootstrap_job failure isolation — a raising collector or store degrades
     to a health issue, NEVER an unhandled exception.
  5. run_tier2_job stitches everything together end-to-end: baseline + reporting stores
     BOTH threaded to run_tier2_check when env is set.

The tier2 sweep and baseline bootstrap are the two production job entrypoints affected
by Phase B — this file pins their env-gate behavior so a bad flag flip during deploy
gets caught here first, not on live capacity.
"""
from datetime import datetime, timezone
from unittest.mock import patch

from fabric_audit_agent.job import (
    run_tier2_job, run_baseline_bootstrap_job, baseline_bootstrap_main,
)
from fabric_audit_agent.context_user_baseline import create_user_baseline_store_memory
from fabric_audit_agent.context_capacity_reporting import (
    create_capacity_reporting_store_memory,
)
from fabric_audit_agent.context_alerts import create_alerts_store_memory


# --- Env-gate behavior on run_tier2_job -------------------------------------------------

_MIN_ENV = {
    # No Delta catalog/schema — Delta-backed stores skip cleanly.
    # No POWER_AUTOMATE_ALERT_URL — delivery skips.
    # No TIER2_WEBHOOK_ENABLED — same.
}


def _fake_collector(facts):
    return {"collect": lambda: facts}


def test_tier2_job_default_env_wires_neither_baseline_nor_reporting():
    """Existing prod env has no BASELINE flag and no REPORTING flag; nothing changes about
    what was live before Phase B. run_tier2_check is invoked without either store."""
    facts = {"capacity": {"peakCuPct": 50.0, "throttleMinutes": 0.0},
             "items": [], "events": []}
    captured = {}

    with patch("fabric_audit_agent.automation.tier2_check.run_tier2_check", return_value={
            "triggered": False, "triggers": [], "delivered": {}, "checkedAt": "t"}) \
            as fake_job:
        run_tier2_job(env=dict(_MIN_ENV), collector=_fake_collector(facts))
        args, kwargs = fake_job.call_args
        captured["baseline_store"] = kwargs.get("baseline_store")
        captured["reporting_store"] = kwargs.get("reporting_store")

    # No catalog + reporting flag defaults on but skips silently when catalog missing.
    assert captured["baseline_store"] is None
    assert captured["reporting_store"] is None


def test_tier2_job_baseline_flag_without_catalog_still_skips():
    """Safety: even with TIER2_BASELINE_ENABLED=1, missing Delta catalog means the store
    can't be constructed. The job must SKIP the wire cleanly (no crash), not attempt to
    build a Delta store against nothing."""
    env = dict(_MIN_ENV)
    env["TIER2_BASELINE_ENABLED"] = "1"
    facts = {"capacity": {}, "items": [], "events": []}

    with patch("fabric_audit_agent.automation.tier2_check.run_tier2_check", return_value={
            "triggered": False, "triggers": [], "delivered": {}, "checkedAt": "t"}) \
            as fake:
        run_tier2_job(env=env, collector=_fake_collector(facts))
        assert fake.call_args.kwargs.get("baseline_store") is None


def test_tier2_job_reporting_flag_off_disables_the_wire():
    """Env override TIER2_REPORTING_ENABLED=0 opts a run out of archival writes — an
    investigation-specific bypass, not the default. Verify the flag is honored."""
    env = dict(_MIN_ENV)
    env["FABRIC_DELTA_CATALOG"] = "cat"
    env["FABRIC_DELTA_SCHEMA"] = "sch"
    env["TIER2_REPORTING_ENABLED"] = "0"
    facts = {"capacity": {}, "items": [], "events": []}

    with patch("fabric_audit_agent.automation.tier2_check.run_tier2_check", return_value={
            "triggered": False, "triggers": [], "delivered": {}, "checkedAt": "t"}) \
            as fake:
        # We also need to prevent Delta stores from being materialized (they'd fail with no
        # Spark). Just patch to a no-op.
        with patch("fabric_audit_agent.context_readings.create_readings_store_delta",
                    return_value=None), \
             patch("fabric_audit_agent.context_capacity_reporting.create_capacity_reporting_store_delta",
                    return_value=None), \
             patch("fabric_audit_agent.context_user_baseline.create_user_baseline_store_delta",
                    return_value=None):
            run_tier2_job(env=env, collector=_fake_collector(facts))
            assert fake.call_args.kwargs.get("reporting_store") is None


def test_tier2_job_both_flags_on_with_catalog_threads_both_stores():
    """The intended production state after Phase B deploys: both flags on, catalog set,
    both stores flow through to run_tier2_check."""
    env = dict(_MIN_ENV)
    env["FABRIC_DELTA_CATALOG"] = "cat"
    env["FABRIC_DELTA_SCHEMA"] = "sch"
    env["TIER2_BASELINE_ENABLED"] = "1"
    env["TIER2_REPORTING_ENABLED"] = "1"
    facts = {"capacity": {}, "items": [], "events": []}

    fake_baseline = object()
    fake_reporting = object()

    with patch("fabric_audit_agent.automation.tier2_check.run_tier2_check", return_value={
            "triggered": False, "triggers": [], "delivered": {}, "checkedAt": "t"}) \
            as fake, \
         patch("fabric_audit_agent.context_readings.create_readings_store_delta",
                return_value=None), \
         patch("fabric_audit_agent.context_capacity_reporting.create_capacity_reporting_store_delta",
                return_value=fake_reporting), \
         patch("fabric_audit_agent.context_user_baseline.create_user_baseline_store_delta",
                return_value=fake_baseline):
        run_tier2_job(env=env, collector=_fake_collector(facts))
        kwargs = fake.call_args.kwargs
        assert kwargs["baseline_store"] is fake_baseline
        assert kwargs["reporting_store"] is fake_reporting


def test_tier2_job_baseline_store_init_error_is_recorded_not_fatal():
    """A Delta store constructor that raises during boot must degrade to a health issue
    and let the sweep continue with baseline_store=None (silent detector). No unhandled
    exceptions on the job's happy path."""
    env = dict(_MIN_ENV)
    env["FABRIC_DELTA_CATALOG"] = "cat"
    env["FABRIC_DELTA_SCHEMA"] = "sch"
    env["TIER2_BASELINE_ENABLED"] = "1"
    facts = {"capacity": {}, "items": [], "events": []}

    def _boom(*a, **k):
        raise RuntimeError("no active spark session")

    with patch("fabric_audit_agent.automation.tier2_check.run_tier2_check", return_value={
            "triggered": False, "triggers": [], "delivered": {}, "checkedAt": "t"}) \
            as fake, \
         patch("fabric_audit_agent.context_readings.create_readings_store_delta",
                return_value=None), \
         patch("fabric_audit_agent.context_capacity_reporting.create_capacity_reporting_store_delta",
                return_value=None), \
         patch("fabric_audit_agent.context_user_baseline.create_user_baseline_store_delta",
                side_effect=_boom):
        res = run_tier2_job(env=env, collector=_fake_collector(facts))
        assert fake.call_args.kwargs["baseline_store"] is None
        # health has the recorded issue so a digest can surface it
        assert any("baseline store init" in i for i in res["health"].get("issues", []))


# --- run_baseline_bootstrap_job (nightly wheel-task) -------------------------------------


def _events(user, values, ts_prefix="2026-08-05T13:{:02d}:00Z"):
    """Fixture: N events for one user across N minutes. Each carries a unique ts so a
    real Delta table would preserve ordering (memory store doesn't care but it's honest)."""
    return [{"user": user, "cuSeconds": v, "ts": ts_prefix.format(i % 60)}
            for i, v in enumerate(values)]


def test_bootstrap_pure_di_path_writes_expected_rows():
    """A hand-injected collector + memory store bypasses every environment lookup. Exercises
    the pure orchestration path — same one prod uses, minus the env plumbing."""
    events = _events("a@x", list(range(1, 26))) + _events("b@x", list(range(1, 26)))
    collector = {"collect": lambda: events}
    store = create_user_baseline_store_memory()

    summary = run_baseline_bootstrap_job(
        env={}, collector=collector, baseline_store=store,
        min_history=20, as_of="2026-08-09T02:00:00Z",
    )
    assert summary["users"] == 2                            # a@x + b@x qualified
    assert summary["hasEstate"] is True
    assert summary["asOf"] == "2026-08-09T02:00:00Z"
    # Health block always populated so a caller can log a single line
    assert "health" in summary
    assert store["get_user"]("a@x")["p95"] is not None
    assert store["get_estate"]() is not None


def test_bootstrap_collector_failure_degrades_to_summary_not_crash():
    """A misbehaving collector must be caught — nightly cron should be idempotent-safe."""
    def _boom():
        raise RuntimeError("LA workspace unreachable")
    collector = {"collect": _boom}
    store = create_user_baseline_store_memory()

    summary = run_baseline_bootstrap_job(
        env={}, collector=collector, baseline_store=store,
        min_history=20, as_of="2026-08-09T02:00:00Z",
    )
    assert summary["rowsWritten"] == 0
    assert "error" in summary
    assert "LA workspace unreachable" in summary["error"]
    # The old baseline (if any) is untouched — we don't clobber it with an empty write
    assert store["_data"] == {}


def test_bootstrap_store_write_failure_recorded_on_health():
    """A store that raises on upsert_many must be caught + recorded so a re-run can spot it,
    but the job returns cleanly."""
    events = _events("a@x", list(range(1, 26)))
    collector = {"collect": lambda: events}
    bad_store = {"get_user": lambda u: None,
                 "get_estate": lambda: None,
                 "upsert_many": lambda rows: (_ for _ in ()).throw(RuntimeError("no perms"))}

    summary = run_baseline_bootstrap_job(
        env={}, collector=collector, baseline_store=bad_store,
        min_history=20, as_of="2026-08-09T02:00:00Z",
    )
    assert "error" in summary
    assert any("baseline bootstrap" in i for i in summary["health"].get("issues", []))


def test_bootstrap_reads_min_history_from_env_when_not_passed():
    """FABRIC_BASELINE_MIN_HISTORY overrides the default 20 — useful for a tenant with a
    denser event stream where a 40-sample floor is a better baseline."""
    events = _events("a@x", list(range(1, 26)))
    collector = {"collect": lambda: events}
    store = create_user_baseline_store_memory()

    # min_history=40 > 25 samples -> user does NOT qualify, but estate row still emits
    summary = run_baseline_bootstrap_job(
        env={"FABRIC_BASELINE_MIN_HISTORY": "40"},
        collector=collector, baseline_store=store,
        as_of="2026-08-09T02:00:00Z",
    )
    assert summary["users"] == 0
    assert summary["hasEstate"] is True                     # cold-start coverage preserved


def test_bootstrap_reads_default_min_history_when_env_missing_or_invalid():
    """A bad env value (non-integer) must NOT crash the job — fall back to the coded
    default 20."""
    events = _events("a@x", list(range(1, 26)))
    collector = {"collect": lambda: events}
    store = create_user_baseline_store_memory()

    summary = run_baseline_bootstrap_job(
        env={"FABRIC_BASELINE_MIN_HISTORY": "not a number"},
        collector=collector, baseline_store=store,
        as_of="2026-08-09T02:00:00Z",
    )
    assert summary["users"] == 1                            # default 20 <= 25 samples


def test_bootstrap_no_store_yields_summary_with_zero_written():
    """If the Delta store cannot be constructed (no catalog / no spark), the job should
    NOT crash — the summary just shows rowsWritten=0 and the caller can retry tomorrow."""
    events = _events("a@x", list(range(1, 26)))
    collector = {"collect": lambda: events}

    # Env has no catalog + no injected store -> Delta init fails silently.
    with patch("fabric_audit_agent.context_user_baseline.create_user_baseline_store_delta",
                side_effect=RuntimeError("no active spark")):
        summary = run_baseline_bootstrap_job(
            env={"FABRIC_DELTA_CATALOG": "cat", "FABRIC_DELTA_SCHEMA": "sch"},
            collector=collector, as_of="2026-08-09T02:00:00Z",
        )
        assert summary["rowsWritten"] == 0
        assert "error" in summary


def test_bootstrap_stamps_wallclock_asof_when_none_passed():
    """Prod cron doesn't pass as_of — the job stamps it. Verify the stamped value is a
    valid ISO-8601 timestamp (not empty string, not None)."""
    events = _events("a@x", list(range(1, 26)))
    collector = {"collect": lambda: events}
    store = create_user_baseline_store_memory()

    summary = run_baseline_bootstrap_job(env={}, collector=collector,
                                          baseline_store=store, min_history=20)
    # asOf must parse as ISO-8601 and be tz-aware
    parsed = datetime.fromisoformat(summary["asOf"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    # And every written row got the same stamp
    row = store["get_user"]("a@x")
    assert row["asOf"] == summary["asOf"]


# --- End-to-end: run_tier2_job with real memory stores ---------------------------------


def test_tier2_end_to_end_with_memory_stores_produces_composite_card_and_archive():
    """The full B1-B4 story exercised in-memory: memory baseline store pre-populated with
    a personalized row; memory reporting store attached; a synthetic collector returning
    a capacity throttle + a user spike. Expect: ONE composite Teams card naming the driver,
    ONE archival row appended to reporting."""
    baseline = create_user_baseline_store_memory([
        {"scope": "user", "user": "bipin@x", "p50": 100.0, "p95": 1000.0, "count": 30,
         "min": 10.0, "max": 1200.0, "asOf": "2026-08-05T00:00:00Z"},
    ])
    reporting = create_capacity_reporting_store_memory()
    alerts = create_alerts_store_memory()
    posts = []
    sink = {"deliver": lambda b: (posts.append(b), {"delivered": True, "status": 202})[1]}

    facts = {
        "capacity": {"peakCuPct": 210.0, "peakAt": "2026-08-05T13:52:00Z",
                      "throttleMinutes": 5.0, "capacityId": "cap-A",
                      "maxInteractiveDelayPct": 82.0},
        "items": [{"name": "R1"}],
        "events": [{"user": "bipin@x", "cuSeconds": 8000.0, "item": "Sales",
                     "operation": "ExecuteQuery", "ts": "2026-08-05T13:53:00Z"}],
    }

    from fabric_audit_agent.automation.tier2_check import run_tier2_check
    from fabric_audit_agent.automation.materiality import load_cfg
    cfg = load_cfg()
    cfg["hysteresis_ticks"] = 1

    res = run_tier2_check(
        {"collect": lambda: facts},
        delivery_sinks={"webhook": sink}, alerts_store=alerts,
        reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True},
        chat_writer=lambda md, title: "c1", app_url="https://app",
        now_dt=datetime(2026, 8, 5, 13, 52, 0, tzinfo=timezone.utc),
        config={}, baseline_store=baseline, reporting_store=reporting,
    )

    # 1. ONE composite Teams card
    assert res["triggered"] is True
    assert len(posts) == 1
    card_text = str(posts[0]["attachments"][0]["content"]["body"])
    assert "Capacity incident" in card_text
    # 2. Correlated spike is named on the card (B3)
    assert "bipin@x" in card_text
    # 3. ONE archival row landed (B4)
    rows = reporting["recent"](10)
    assert len(rows) == 1
    r = rows[0]
    assert r["peakCuPct"] == 210.0
    # signalTypes captured — includes throttle, pressure, extreme_peak, throttle_imminent
    # plus the composite ("capacity_incident") they coalesced into.
    assert set(r["signalTypes"]).issuperset({"throttle", "pressure", "extreme_peak"})


def test_baseline_bootstrap_main_FAILS_LOUDLY_when_env_absent():
    """The wheel-task entry must go RED when it cannot do its job.

    Every internal path returns a summary instead of raising (so a transient outage can't crash
    mid-write), but a nightly job that writes ZERO rows and still reports SUCCESS is invisible:
    the 5-min sweep would keep comparing against a weeks-old baseline while the Jobs UI showed
    green. `baseline_bootstrap_main` therefore re-raises on error / zero rows so the job's
    failure notification actually fires."""
    import pytest
    with patch("fabric_audit_agent.job.os.environ", {}):
        with patch("fabric_audit_agent.job._run_startup_preflight"):
            with patch("fabric_audit_agent.job._check_startup_invariant"):
                with patch("fabric_audit_agent.job._alert_failure"):
                    with pytest.raises(RuntimeError, match="baseline bootstrap"):
                        baseline_bootstrap_main()


def test_baseline_bootstrap_main_raises_on_zero_rows_even_without_error():
    """A clean run that legitimately found no activity still writes nothing — that is a
    failure to surface, not a success."""
    import pytest
    with patch("fabric_audit_agent.job._run_startup_preflight"), \
         patch("fabric_audit_agent.job._check_startup_invariant"), \
         patch("fabric_audit_agent.job._alert_failure"), \
         patch("fabric_audit_agent.job.run_baseline_bootstrap_job",
                return_value={"rowsWritten": 0, "users": 0, "hasEstate": False,
                              "asOf": "t", "health": {}}):
        with pytest.raises(RuntimeError, match="0 rows"):
            baseline_bootstrap_main()
