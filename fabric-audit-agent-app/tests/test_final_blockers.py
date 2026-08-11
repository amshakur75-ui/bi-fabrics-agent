"""The three blockers the final certification pass found, and the raise it found untested.

All three were regressions or half-fixes in code written THIS session, and none was caught by the
2425 tests that existed: each one had a guarding test that asserted the right thing about the wrong
object. Every test here drives production code with production-shaped data.
"""
import pytest

from fabric_audit_agent.adapters.reasoner_investigation import create_investigation_reasoner
from fabric_audit_agent.investigation.overloads import overload_windows
from fabric_audit_agent.investigation.playbooks import (
    investigate_capacity_spike, investigate_user)
from fabric_audit_agent.severity import DEFAULT_CONFIG, score_severity


# ---- B1: an unmeasurable share must not crash, and must not read as zero ----

def _cost_less_facts():
    """The rollup shape when NO cost column resolved -- a schema rename, a frequency-only source, or
    a query whose cost alias changed. `sharePct` is deliberately None to say so honestly."""
    return {"capacity": {"peakCuPct": 210.0, "throttleMinutes": 8.0},
            "items": [{"name": "Ent-Reporting-DTC", "workspace": "Ent-Reporting",
                       "sharePct": None, "cuSeconds": None,
                       "topUsers": [{"user": "aaron@newellco.com", "cuSeconds": None}],
                       "attributionMode": "cost-cpu", "shareBasis": "unavailable",
                       "userCount": 1, "truncated": False}],
            "users": [{"user": "aaron@newellco.com", "sharePct": None,
                       "topItems": ["Ent-Reporting-DTC"]}]}


def _measured_facts():
    f = _cost_less_facts()
    f["items"][0].update(sharePct=64.0, cuSeconds=4200.0, shareBasis="cost")
    f["items"][0]["topUsers"] = [{"user": "aaron@newellco.com", "cuSeconds": 3600.0}]
    f["users"][0]["sharePct"] = 64.0
    return f


@pytest.mark.parametrize("name,call", [
    ("investigate_user",
     lambda facts: investigate_user({"collect": lambda: facts}, create_investigation_reasoner(),
                                    "aaron@newellco.com")),
    ("investigate_capacity_spike",
     lambda facts: investigate_capacity_spike({"collect": lambda: facts},
                                              create_investigation_reasoner())),
])
def test_the_flagship_tools_survive_an_unmeasurable_share(name, call):
    """`.get("sharePct", 0)` defaults a MISSING key, not a key present with None -- so `round(None)`
    raised TypeError and took out both flagship investigation tools in the deployed chat app, with
    no try/except anywhere in the tool loop, on exactly the data condition the None was introduced
    to describe. The guarding tests covered the PRODUCER of the None and the concentration detector;
    nothing drove it through these two."""
    text = str(call(_cost_less_facts()))
    assert "unmeasured" in text, "the report must say the share could not be measured"


@pytest.mark.parametrize("name,call", [
    ("investigate_user",
     lambda facts: investigate_user({"collect": lambda: facts}, create_investigation_reasoner(),
                                    "aaron@newellco.com")),
    ("investigate_capacity_spike",
     lambda facts: investigate_capacity_spike({"collect": lambda: facts},
                                              create_investigation_reasoner())),
])
def test_a_measured_share_is_still_reported_as_a_number(name, call):
    """The fix must not turn every share into prose."""
    assert "64.0%" in str(call(_measured_facts()))


def test_an_unmeasurable_share_is_never_rendered_as_zero_percent():
    """Coercing the None to 0 would have been worse than the crash: "aaron = 0.0% of monitored CU"
    states that a named person contributed nothing, which is a claim from no evidence."""
    text = str(investigate_user({"collect": _cost_less_facts},
                                create_investigation_reasoner(), "aaron@newellco.com"))
    assert "= 0.0%" not in text and "= 0%" not in text


def test_severity_does_not_crash_and_does_not_invent_a_share():
    """`None >= int` raises, and the reason line would otherwise read "None% of monitored CU"."""
    res = score_severity({"type": "capacity.concentration",
                          "evidence": {"sharePct": None, "attributionMode": "cost-cpu"}},
                         DEFAULT_CONFIG)
    assert res["level"] == "Warning", "an unmeasurable share cannot clear a Critical threshold"
    assert "None%" not in res["reason"] and "unmeasured" in res["reason"]


@pytest.mark.parametrize("share,level", [(62.0, "Critical"), (20.0, "Warning")])
def test_a_measured_concentration_still_grades_normally(share, level):
    res = score_severity({"type": "capacity.concentration",
                          "evidence": {"sharePct": share, "attributionMode": "cost-cpu"}},
                         DEFAULT_CONFIG)
    assert res["level"] == level
    assert f"{share}%" in res["reason"]


def _list_workspaces(monkeypatch, facts):
    """list_workspaces builds its own collector from env (`_build_collector`) and ignores an injected
    one, so the env-built collector is what has to be replaced to reach the grouping code."""
    from fabric_audit_agent import tools as tools_mod
    monkeypatch.setattr(tools_mod, "_build_collector", lambda env: {"collect": lambda: facts})
    defs = {t["name"]: t for t in tools_mod.create_tool_definitions({"collect": lambda: facts})}
    return defs["list_workspaces"]["handler"]({})


def test_list_workspaces_passes_an_unknown_share_through_as_null(monkeypatch):
    """Same trap, third call site. `round(None, 1)` took out the whole tool; `or 0` would have told
    the caller the item used none of the capacity."""
    out = _list_workspaces(monkeypatch, _cost_less_facts())
    items = [i for ws in out.get("workspaces", []) for i in ws.get("items", [])]
    assert items, f"the fixture did not reach the grouping code: {out.get('note') or out}"
    assert all(i.get("sharePct") is None for i in items), (
        f"an unmeasurable share must stay null, got {[i.get('sharePct') for i in items]}")


def test_list_workspaces_still_rounds_a_measured_share(monkeypatch):
    out = _list_workspaces(monkeypatch, _measured_facts())
    items = [i for ws in out.get("workspaces", []) for i in ws.get("items", [])]
    assert items and items[0]["sharePct"] == 64.0


# ---- B2: one implementation, so a fix cannot land in a subset ---------------

def _all_three():
    from agent_server.loop_hooks import normalize_executing_user_display as a
    from fabric_audit_agent.export.html_report import normalize_executing_user_display as b
    from fabric_audit_agent.export.xlsx_report import normalize_executing_user_display as c
    return {"loop_hooks": a, "export/html_report": b, "export/xlsx_report": c}


@pytest.mark.parametrize("raw", [
    "b3f2c1d4-1111-2222-3333-99887766aaaa",
    "{b3f2c1d4-1111-2222-3333-99887766aaaa}",
    "svc-refresh-agent",
    "Power BI Service",
])
def test_no_surface_invents_a_person_who_does_not_exist(raw):
    """The fix landed in the chat table only, so the fabrication MOVED into the exports rather than
    stopping -- and the exports are the artifact people forward to other people. The test that
    asserted the fix said "and in every export" in its own docstring while importing one of the
    three copies."""
    for where, fn in _all_three().items():
        assert fn(raw) == raw, f"{where} fabricated an address for {raw!r}: {fn(raw)!r}"


@pytest.mark.parametrize("raw,expected", [
    ("aaron", "aaron@newellco.com"),
    ("aaron.mohamed", "aaron.mohamed@newellco.com"),
    ("aaron@newellco.com", "aaron@newellco.com"),
    ("someone@other.com", "someone@other.com"),
    ("", ""),
    (None, ""),
])
def test_every_surface_agrees_on_the_normal_cases_too(raw, expected):
    for where, fn in _all_three().items():
        assert fn(raw) == expected, f"{where} disagrees on {raw!r}"


def test_there_is_exactly_one_implementation():
    """The structural guarantee. Three copies is what allowed a one-of-three fix; if someone
    reintroduces a local copy, this fails."""
    fns = list(_all_three().values())
    assert len({f.__module__ for f in fns}) == 1, (
        f"the helper is defined in more than one module: {[f.__module__ for f in fns]}")
    assert fns[0].__module__ == "fabric_audit_agent.identity_display"


# ---- B3: an unknown cost taints every window it spans ----------------------

def test_a_cost_less_operation_makes_every_window_it_spans_unavailable():
    """The `cu is None` branch yielded the first window and then RETURNED, skipping the rest of the
    walk. One 90-second cost-less op over a 420% overage produced one honest window followed by two
    reading "0% user / 420% background" -- verbatim the "system/refresh work, do NOT blame a user"
    exoneration the function's own comment says it closes. The guarding test used a single
    10-second op, which fits in one window and so could never see this."""
    series = [{"epoch": e, "cuPct": 420.0} for e in (0, 30, 60)]
    ops = [{"startEpoch": 0, "endEpoch": 90, "cuSeconds": None, "user": "aaron@newellco.com",
            "item": "Ent-Reporting-DTC", "operation": "QueryEnd"}]
    wins = overload_windows(series, ops, base_cu=64)
    assert len(wins) == 3, f"expected three overload windows, got {len(wins)}"
    for i, w in enumerate(wins):
        assert w["interactiveCuPct"] is None, f"window {i} claimed a user split it cannot know"
        assert w["backgroundCuPct"] is None, f"window {i} exonerated every user from no evidence"
        assert "splitNote" in w


def test_one_cost_less_op_among_costed_ones_still_taints_the_window():
    """Partial knowledge is not knowledge: if any op in the window has no cost, the split for that
    window is not computable."""
    series = [{"epoch": e, "cuPct": 420.0} for e in (0, 30, 60)]
    ops = [{"startEpoch": 0, "endEpoch": 90, "cuSeconds": None, "user": "a@newellco.com",
            "item": "X", "operation": "QueryEnd"},
           {"startEpoch": 0, "endEpoch": 90, "cuSeconds": 300.0, "user": "b@newellco.com",
            "item": "Y", "operation": "QueryEnd"}]
    for w in overload_windows(series, ops, base_cu=64):
        assert w["interactiveCuPct"] is None and w["backgroundCuPct"] is None


def test_a_fully_costed_multi_window_operation_still_splits_every_window():
    """The fix must not make everything unknowable."""
    series = [{"epoch": e, "cuPct": 420.0} for e in (0, 30, 60)]
    ops = [{"startEpoch": 0, "endEpoch": 90, "cuSeconds": 300.0, "user": "b@newellco.com",
            "item": "Y", "operation": "QueryEnd"}]
    wins = overload_windows(series, ops, base_cu=64)
    assert len(wins) == 3
    for w in wins:
        assert w["interactiveCuPct"] is not None and w["backgroundCuPct"] is not None
        assert "splitNote" not in w


# ---- RISK 1: job_main's degraded-raise, behaviourally --------------------

def _job_with(monkeypatch, degraded):
    """Drive job_main with a controlled HealthReport.

    Startup preflight degrades health in any environment without the production secrets, so it has
    to be neutralised or the "healthy" direction cannot be tested at all.
    """
    from fabric_audit_agent import job as job_mod

    monkeypatch.setattr(job_mod, "_check_startup_invariant", lambda health: None)
    monkeypatch.setattr(job_mod, "_run_startup_preflight", lambda env, health: None)

    def fake_run(**kw):
        if degraded:
            kw["health"].record_issue("collector primary FAILED")
        return {"summary": "sweep ok"}

    monkeypatch.setattr(job_mod, "run_unified_job", fake_run)
    return job_mod


def test_a_degraded_sweep_fails_the_run_so_the_failure_email_fires(monkeypatch):
    """The only guard on this was three `inspect.getsource` substring asserts, and a mutation to
    `if False and health.degraded ...` left the whole suite green. The re-raise IS the alerting
    mechanism (each job stanza's email_notifications.on_failure), so an unasserted raise is an
    unasserted alert path -- and this session already shipped a NameError through a gap of exactly
    this shape."""
    job_mod = _job_with(monkeypatch, degraded=True)
    with pytest.raises(RuntimeError, match="degraded"):
        job_mod.job_main()


def test_a_healthy_sweep_does_not_fail_the_run(monkeypatch):
    """The other direction, which matters just as much: a successful, fully delivered sweep must not
    turn the hourly job RED."""
    job_mod = _job_with(monkeypatch, degraded=False)
    assert job_mod.job_main()["summary"] == "sweep ok"


def test_the_degraded_raise_can_be_disabled_by_env(monkeypatch):
    """The documented escape hatch. If a degraded collect is expected for a while, an operator must
    be able to stop paging themselves without editing code."""
    monkeypatch.setenv("FABRIC_FAIL_ON_DEGRADED", "0")
    job_mod = _job_with(monkeypatch, degraded=True)
    assert job_mod.job_main()["summary"] == "sweep ok"


# ---- B1's fourth and fifth call sites, found by testing rather than reading ----

def test_a_workspace_total_skips_an_unpriced_item_and_says_so():
    """One line below the sharePct fix, `entry["totalCuSeconds"] += item.get("cuSeconds", 0)` hit the
    identical present-but-None trap and raised TypeError. Coercing to 0 would have understated the
    workspace total with nothing marking it, so an unpriced item is counted separately instead."""
    facts = {"capacity": {"peakCuPct": 88.0}, "items": [
        {"name": "Priced", "workspace": "Ent", "sharePct": 30.0, "cuSeconds": 1200.0,
         "topUsers": [], "attributionMode": "cost-cpu", "shareBasis": "cost",
         "userCount": 0, "truncated": False},
        {"name": "Unpriced", "workspace": "Ent", "sharePct": None, "cuSeconds": None,
         "topUsers": [], "attributionMode": "cost-cpu", "shareBasis": "unavailable",
         "userCount": 0, "truncated": False}]}
    import pytest as _p
    ws = None
    from fabric_audit_agent import tools as tools_mod

    class _MP:
        def __init__(self): self.old = tools_mod._build_collector
        def __enter__(self):
            tools_mod._build_collector = lambda env: {"collect": lambda: facts}
            return self
        def __exit__(self, *a): tools_mod._build_collector = self.old

    with _MP():
        defs = {t["name"]: t for t in tools_mod.create_tool_definitions({"collect": lambda: facts})}
        out = defs["list_workspaces"]["handler"]({})
    ws = out["workspaces"][0]
    assert ws["totalCuSeconds"] == 1200.0, "the total must count only what was priced"
    assert ws.get("itemsWithoutCost") == 1, "and must disclose what it could not price"


def test_user_spike_history_survives_events_with_no_cost():
    """user_spike_history is an MCP tool the agent quotes directly, and it raised TypeError on the
    most ordinary event source it has: tier-1 activity events carry cuSeconds=None on EVERY row, a
    fact the codebase documents in three places. `sorted`, `sum` and `max` all break on None."""
    from fabric_audit_agent.investigation.spike_history import user_spike_history

    ev = [{"user": "a@newellco.com", "item": "D", "cuSeconds": None, "kind": "interactive",
           "ts": f"2026-08-10T09:{i:02d}:00Z"} for i in range(6)]
    res = user_spike_history(ev, "a@newellco.com")
    assert res["eventsWithoutCost"] == 6
    assert res["cuAggregatesComplete"] is False, (
        "a total computed over zero priced rows must not present itself as complete")


def test_user_spike_history_discloses_a_partial_cost_signal():
    """The dangerous case is not all-or-nothing, it is MOST: a total over a third of the rows looks
    like a real answer."""
    from fabric_audit_agent.investigation.spike_history import user_spike_history

    ev = [{"user": "a@newellco.com", "item": "D", "cuSeconds": None, "kind": "interactive",
           "ts": f"2026-08-10T09:{i:02d}:00Z"} for i in range(6)]
    ev.append({"user": "a@newellco.com", "item": "X", "cuSeconds": 900.0, "kind": "interactive",
               "ts": "2026-08-10T09:30:00Z"})
    res = user_spike_history(ev, "a@newellco.com")
    assert res["totalCuSeconds"] == 900.0
    assert res["eventsWithoutCost"] == 6 and res["cuAggregatesComplete"] is False


def test_a_fully_priced_history_reports_complete_aggregates():
    from fabric_audit_agent.investigation.spike_history import user_spike_history

    ev = [{"user": "a@newellco.com", "item": "D", "cuSeconds": 100.0 * (i + 1),
           "kind": "interactive", "ts": f"2026-08-10T09:{i:02d}:00Z"} for i in range(6)]
    res = user_spike_history(ev, "a@newellco.com")
    assert res["cuAggregatesComplete"] is True and res["eventsWithoutCost"] == 0
    assert res["totalCuSeconds"] == 2100.0 and res["peakCuSeconds"] == 600.0


# ---- the readings order the blindness alarm depends on ----------------------

def test_recent_returns_newest_first_even_when_timestamps_collide():
    """`sorted(..., reverse=True)` is STABLE, so rows sharing a runAt came back in insertion order --
    oldest-first, the inverse of this function's documented contract -- while
    _check_silent_failure reads `readings[:n]` believing element 0 is the newest. Equal timestamps
    are not exotic: datetime.now() has ~15ms granularity on Windows, so any two appends in the same
    tick collide. Observed effect: the blindness alarm could not clear after the collector recovered.
    """
    from fabric_audit_agent.context_readings import create_readings_store_memory

    store = create_readings_store_memory()
    for i in range(6):                              # identical runAt, ascending marker
        store["append"]({"runAt": "2026-08-10T09:00:00.000000Z", "marker": i,
                         "collectorOk": i >= 3, "capacityOk": i >= 3})
    got = [r["marker"] for r in store["recent"](3)]
    assert got == [5, 4, 3], f"recent() must be newest-first on ties, got {got}"


def test_the_blindness_alarm_clears_once_the_collector_recovers():
    """The behavioural consequence: three good readings after a blind streak must silence it."""
    from fabric_audit_agent.automation.tier2_check import _check_silent_failure

    blind = [{"runAt": "2026-08-10T09:00:00.000000Z", "collectorOk": False, "capacityOk": False}]
    good = [{"runAt": "2026-08-10T09:00:00.000000Z", "collectorOk": True, "capacityOk": True}]
    assert _check_silent_failure(blind * 3) != [], "three blind readings must raise it"
    assert _check_silent_failure(good * 3 + blind * 8) == [], "and three good ones must clear it"
