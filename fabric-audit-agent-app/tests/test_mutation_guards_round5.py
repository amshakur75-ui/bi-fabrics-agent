"""Round-5 mutation guards: the guards that rounds 3 and 4 ADDED, attacked in their turn.

Every round of this audit has found a defect inside a recently-written fix, so the fixes are the
place to look. Twenty-eight plausible-refactor mutations were applied to the round-3/4 guards one
at a time against the full suite; twenty-one died on existing tests. The seven below survived, and
each survivor silently reverts a fix while every run stays green:

  1. the concentration minimum-activity floor was a MINIMUM, not a "must exceed" — a window
     carrying exactly the floor's worth of work fell silent.
  2. the ambiguous->informational row carried no presence streak, so it cycled back through
     pending instead of settling (the same defect round 4 fixed on the RECURRING branch; the
     ambiguous branch is a second, independent copy of the upsert).
  3. ``judged`` was read for presence rather than truth, so a reasoner that declared itself
     UNABLE to judge was treated as having judged.
  4. ``cpuMs`` is true CPU time; grouping it with the DurationMs fallback mislabels every
     production item as proxy-derived.
  5. the LA timestamp parse must stay on the canonical parser — the failure it fixes is
     invisible on this interpreter and only bites the 3.10 job compute.
  6. only a STILL-FIRING capacity row may raise the digest headline; the caller's filter is
     where that is decided.
  7. a degraded tier2 run must fail the Databricks run, because the raise is the only thing that
     makes ``email_notifications.on_failure`` fire.

A separate finding, recorded here because it belongs with these: the round-3 commit message
claims ``_send`` records a health delivery failure on ``delivered=False`` and that a still-firing
incident whose card never landed re-sends. Neither exists in the code (no commit in history ever
added them), so a Teams card lost to an HTTP 500 is still lost silently. That is a missing fix,
not a missing test, so it is reported rather than tested here.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from fabric_audit_agent import job as job_mod
from fabric_audit_agent.adapters import attribution_rollup
from fabric_audit_agent.adapters.attribution_rollup import rollup_attribution
from fabric_audit_agent.automation.daily_summary import run_daily_summary
from fabric_audit_agent.automation.materiality import load_cfg
from fabric_audit_agent.automation.tier2_check import _check_concentration, process_alerts
from fabric_audit_agent.context_alerts import _from_row, _to_row, create_alerts_store_memory

T0 = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _delta_faithful_store():
    """Delta-faithful store answering all three status queries.

    Same shape as ``test_mutation_guards_round4._delta_faithful_store``:
    ``create_delta_faithful_store`` stubs ``query_pending``/``query_informational`` as
    ``lambda: {}``, which makes hysteresis and the informational tier permanently stateless —
    and statelessness is precisely what hides a row that cycles instead of settling. Rows still
    round-trip through the real ``_to_row``/``_from_row``, so a field the state machine writes
    but ``_FIELDS`` does not map vanishes here like it does in Unity Catalog.
    """
    data = {}

    def _by_status(status):
        return {k: dict(v) for k, v in data.items() if v.get("status") == status}

    def upsert(alert):
        data[alert["incidentKey"]] = _from_row(_to_row(alert))

    def resolve(key, at):
        cur = data.get(key)
        if cur is not None and cur.get("status") == "active":
            cur["status"] = "resolved"
            cur["resolvedAt"] = at

    return {"query_active": lambda: _by_status("active"),
            "query_pending": lambda: _by_status("pending"),
            "query_informational": lambda: _by_status("informational"),
            "upsert": upsert, "resolve": resolve,
            "delete": lambda k: data.pop(k, None), "_data": data}


def _sink():
    posts = []
    return posts, {"deliver": lambda b: (posts.append(b), {"delivered": True, "status": 202})[1]}


def _kw(store, sink, **over):
    cfg = load_cfg()
    cfg.update(over.pop("cfg", {}))
    return dict(alerts_store=store, delivery_sinks={"webhook": sink},
                chat_writer=lambda m, t: "c1", app_url="https://app", cfg=cfg, **over)


# --- 1. the activity floor is a MINIMUM, not a "must exceed" ------------------------------

def _priced_item(name, share, cu):
    return {"name": name, "workspace": "Ent", "sharePct": share, "cuSeconds": cu,
            "topUsers": [{"user": "u1@co", "cuSeconds": cu}], "attributionMode": "cost-cpu"}


def test_a_window_carrying_exactly_the_floors_worth_of_work_still_alerts():
    """``min_window_cu`` answers "is there enough total cost here for a share to mean anything?"
    and 60 CU-s is the answer's calibrated yes (~1 CU busy for a minute), not its exclusive
    boundary. Slipping ``<`` to ``<=`` makes the floor silently one increment stricter, and the
    thing it silences is a REAL 91%-of-a-busy-window concentration — the product's headline alert
    — with no trace anywhere that a suppression happened."""
    at_the_floor = {"capacity": {"peakCuPct": 88.0},
                    "items": [_priced_item("DTC", 91.7, 55.0), _priced_item("Sales", 8.3, 5.0)]}
    assert load_cfg()["min_window_cu"] == 60.0          # the calibration this test is pinned to
    trigs = _check_concentration(at_the_floor)
    assert [t["item"] for t in trigs] == ["DTC"], "a window AT the floor was silenced"

    # ...and the floor is still a floor: a hair under it stays quiet.
    under = {"capacity": {"peakCuPct": 88.0},
             "items": [_priced_item("DTC", 91.7, 54.8), _priced_item("Sales", 8.3, 5.0)]}
    assert _check_concentration(under) == []


# --- 2. the AMBIGUOUS informational row must settle, not cycle ----------------------------

def _concentration(share):
    return {"check": "concentration", "item": "Sales Report", "workspace": "Finance",
            "sharePct": share, "owner": "owner@x", "topUsers": [{"user": "u1"}]}


def test_an_ambiguous_pattern_settles_informational_instead_of_cycling(capsys):
    """Round 4 fixed this cycling on the RECURRING-attribution branch. The ambiguous branch is a
    SECOND, independently written copy of the same informational upsert, and dropping its
    ``presenceCount`` reproduces the whole defect there: the next tick's hysteresis block sees no
    streak, computes count=1, and rewrites the row back to status='pending'. An already-classified
    moderate pattern is then re-promoted through the persistence gate every three sweeps, so the
    digest's "stable patterns" section shows it on roughly one tick in three and the notification
    centre flickers between tiers for something that has not changed at all.

    Multi-tick on purpose: nothing is wrong until the tick AFTER the row goes informational.
    A 35% share is the middle materiality tier (33-40%), and the reasoner here does NOT declare
    ``judged``, so this is the production routing, not a contrived one.
    """
    store = _delta_faithful_store()
    posts, sink = _sink()
    kw = _kw(store, sink, reasoner=lambda t: {"markdown": "m", "summary": "s", "report": True})
    key = "concentration::Finance/Sales Report"

    statuses = []
    for i in range(9):        # ~45 minutes of the same unchanged moderate pattern
        process_alerts([_concentration(35.0)], now_dt=T0 + timedelta(minutes=5 * i), **kw)
        statuses.append(store["_data"][key]["status"])

    assert "informational" in statuses, "a 35% share never reached the middle tier"
    settled = statuses[statuses.index("informational"):]
    assert set(settled) == {"informational"}, f"the row cycled instead of settling: {statuses}"
    assert posts == [], "an ambiguous pattern paged the Teams channel"


# --- 3. `judged` is read for TRUTH, not for presence --------------------------------------

def test_a_reasoner_that_declares_it_could_not_judge_does_not_get_to_promote():
    """``judged`` is the reasoner's own statement that it is CAPABLE of the ambiguous verdict —
    the whole point being that the deployed v1 reasoner is a facts renderer whose ``report: True``
    is a constant, not an opinion. Testing the key for presence instead of truth hands the verdict
    straight back to any reasoner that carries the key at all, including one honestly reporting
    ``judged: False`` after an LLM error fell back to the KB renderer. The middle tier then
    disappears again exactly as it did in production, and a 110% blip cards as "Capacity incident".
    """
    store = create_alerts_store_memory()
    posts, sink = _sink()
    acts = process_alerts(
        [{"check": "pressure", "peakCuPct": 110.0}], now_dt=T0,
        **_kw(store, sink, reasoner=lambda t: {"markdown": "m", "summary": "s",
                                               "report": True, "judged": False}))
    assert posts == [], "a self-declared NON-judgement promoted an ambiguous trigger to a card"
    assert acts["informational"] == ["capacity::capacity"]
    assert acts["new"] == []


# --- 4. cpuMs IS true CPU time -----------------------------------------------------------

def test_cpu_ms_counts_as_true_cpu_not_as_the_duration_fallback():
    """The deployed Log Analytics query ends ``| summarize cpuMs=sum(CpuTimeMs) by ...`` — cpuMs
    IS CpuTimeMs under an alias. Treating it as the DurationMs proxy made ``is_true_cpu`` False for
    EVERY row in production, so every item and user shipped attributionMode="cost-duration" while
    the numbers were genuine CPU time. It errs safe in one direction only: the proxy/true-CU
    indicator, the confidence badge and the card copy all read this field, so the product
    understated its own evidence on every single sweep."""
    row = {"ItemName": "M", "Workspace": "W", "ExecutingUser": "u@x", "cpuMs": 5000}
    out = rollup_attribution([row])
    assert out["items"][0]["attributionMode"] == "cost-cpu"
    assert out["items"][0]["cuSeconds"] == 5.0          # ms -> CU-seconds, unchanged by the label

    # The alias must not drag the real fallback up with it: DurationMs is still a proxy.
    proxy = rollup_attribution([{"ItemName": "M", "Workspace": "W", "ExecutingUser": "u@x",
                                 "DurationMs": 5000}])
    assert proxy["items"][0]["attributionMode"] == "cost-duration"


# --- 5. the LA timestamp parse stays on the canonical parser -----------------------------

def test_the_timestamp_parse_delegates_to_the_canonical_parser(monkeypatch):
    """This guard's failure is INVISIBLE here, which is why it needs pinning by delegation rather
    than by behaviour. Real ``TimeGenerated`` values carry SEVEN fractional digits
    (2026-08-05T13:52:07.3079171Z); ``datetime.fromisoformat`` accepts only 3 or 6 on Python 3.10,
    which is what the serverless JOB COMPUTE runs — and the sweep job is where Log Analytics rows
    are collected. Every parse raised, was swallowed, returned None, and silently skipped the B2
    activity cross-reference; locally on 3.12 and in the App on 3.11 it worked, so nothing ever
    surfaced it. ``timefmt.parse_iso_utc`` trims the fraction to microseconds first and exists for
    exactly this, so a hand-rolled replacement would pass every behavioural test on this
    interpreter while breaking production again."""
    assert attribution_rollup._parse_ts("2026-08-05T13:52:07.3079171Z") == datetime(
        2026, 8, 5, 13, 52, 7, 307917, tzinfo=timezone.utc)

    sentinel = object()
    seen = []
    monkeypatch.setattr(attribution_rollup, "parse_iso_utc",
                        lambda v: (seen.append(v), sentinel)[1])
    assert attribution_rollup._parse_ts("2026-08-05T13:52:07.3079171Z") is sentinel
    assert seen == ["2026-08-05T13:52:07.3079171Z"]


# --- 6. only a STILL-FIRING capacity row raises the digest headline ----------------------

def _capacity_row(*, firing):
    return {"incidentKey": "capacity::cap-1", "status": "active", "severity": "warn",
            "checkType": "capacity_incident", "resource": "capacity",
            "currentlyActive": firing}


def _digest(row):
    store = create_alerts_store_memory({row["incidentKey"]: row})
    posts, sink = _sink()
    run_daily_summary(alerts_store=store, delivery_sinks={"webhook": sink},
                      capacity={"peakCuPct": 42.0}, app_url="https://app",
                      now_dt=datetime(2026, 8, 10, 7, 0, tzinfo=timezone.utc))
    return json.dumps(posts)


def test_only_a_still_firing_capacity_row_raises_the_digest_headline():
    """The headline gate lives in ``build_daily_summary``, but WHICH rows reach it is decided by
    the caller's ``currentlyActive`` filter — and that filter is the difference between "a capacity
    incident is happening right now" and "somebody never clicked Resolve on one from last week".
    Drop it and the digest opens with a live-incident warning every single morning, which is the
    stale-backlog flood that produced "Open tickets: 161" wearing a scarier hat: once the banner
    is permanent it stops meaning anything, and the morning it IS real nobody looks."""
    stale = _digest(_capacity_row(firing=False))
    assert "No significant issues" in stale
    assert "capacity incident" not in stale

    # The counterpart — the filter must not become a gag order on a genuinely live incident.
    live = _digest(_capacity_row(firing=True))
    assert "No significant issues" not in live
    assert "capacity incident" in live


# --- 7. a degraded tier2 run must fail the Databricks run --------------------------------

def _degrading_tier2(monkeypatch, detail="alerting unwired: no webhook sink"):
    monkeypatch.setattr(job_mod, "_check_startup_invariant", lambda health: None)
    monkeypatch.setattr(job_mod, "_run_startup_preflight", lambda env, health: None)

    def fake(*, env, health):
        health.record_issue(detail)
        return {"triggered": False, "triggers": [], "delivered": {}}

    monkeypatch.setattr(job_mod, "run_tier2_job", fake)


def test_a_degraded_tier2_run_fails_the_job_so_the_failure_email_fires(monkeypatch, capsys):
    """The re-raise IS the alerting path: ``_alert_failure`` is a stub, so the only thing that
    reaches a human when tier2 degrades is Databricks marking the run FAILED and the job stanza's
    ``email_notifications.on_failure``. Returning normally puts the diagnosis in stdout nobody
    reads and reports TERMINATED SUCCESS — the exact shape of the 2026-08-09 incident, where
    capacity alerting was dead for 5.5 hours while every run showed green."""
    _degrading_tier2(monkeypatch)
    monkeypatch.delenv("FABRIC_FAIL_ON_DEGRADED", raising=False)
    with pytest.raises(RuntimeError, match="degraded"):
        job_mod.tier2_main()
    # AFTER the work and the diagnosis, never instead of them: the operator needs both the email
    # and the reason, and the alert pass must still have completed.
    assert "alerting unwired" in capsys.readouterr().out


def test_the_degraded_raise_has_a_documented_opt_out(monkeypatch):
    """A transient sub-source flapping the job red must be survivable without deleting the guard,
    or the guard gets deleted."""
    _degrading_tier2(monkeypatch)
    monkeypatch.setenv("FABRIC_FAIL_ON_DEGRADED", "0")
    assert job_mod.tier2_main()["triggered"] is False


def test_a_healthy_tier2_run_does_not_fail(monkeypatch):
    """No false positives: the raise is conditional on ``health.degraded``, not on running."""
    monkeypatch.setattr(job_mod, "_check_startup_invariant", lambda health: None)
    monkeypatch.setattr(job_mod, "_run_startup_preflight", lambda env, health: None)
    monkeypatch.setattr(job_mod, "run_tier2_job",
                        lambda *, env, health: {"triggered": True, "triggers": [{"check": "x"}],
                                                "delivered": {}})
    monkeypatch.delenv("FABRIC_FAIL_ON_DEGRADED", raising=False)
    assert job_mod.tier2_main()["triggered"] is True
