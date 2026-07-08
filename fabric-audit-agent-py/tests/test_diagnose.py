"""Diagnose decision-tree engine (pure) — executable runbooks that confirm AND eliminate."""
import pytest

from fabric_audit_agent.investigation.diagnose import (
    run_diagnosis, diagnose_throttle, diagnose_refresh, diagnose_slowness,
)

_SERIES_CALM = [{"ts": f"2026-07-07T09:{m:02d}:00Z", "cuPct": 60.0} for m in range(10)]

# Hot series with a signal that fires (interactiveDelayPct>100) in the over-window.
_SERIES_HOT = ([{"ts": "2026-07-07T09:00:00Z", "cuPct": 80.0}]
               + [{"ts": f"2026-07-07T09:{m:02d}:00Z", "cuPct": 130.0, "interactiveDelayPct": 120.0}
                  for m in (1, 2, 3)]
               + [{"ts": "2026-07-07T09:04:00Z", "cuPct": 70.0}])

_DAX_TEXT = "CALCULATE(SUM(Sales[Amount]), FILTER(Sales, Sales[Region]=\"West\"))"

_EVENTS_WITH_COLLISION = [
    {"ts": "2026-07-07T09:02:00Z", "user": "john@co", "item": "Sales", "kind": "interactive",
     "cuSeconds": 90.0, "queryText": _DAX_TEXT},
    {"ts": "2026-07-07T09:02:30Z", "user": "svc@co", "item": "Sales Model", "kind": "refresh",
     "cuSeconds": 40.0},
]

_EVENTS_NO_COLLISION = [
    {"ts": "2026-07-07T09:02:00Z", "user": "john@co", "item": "Sales", "kind": "interactive",
     "cuSeconds": 90.0, "queryText": "SUM(Sales[Amount])"},
]


def test_throttle_calm_series_eliminates_at_step1_high_confidence_no_root_cause():
    chain = diagnose_throttle(_SERIES_CALM, _EVENTS_NO_COLLISION)
    assert chain["symptom"] == "throttle"
    assert chain["chain"][0]["verdict"] == "eliminated"
    assert chain["rootCause"] is None
    assert chain["eliminated"] == ["capacity throttling"]
    assert chain["confidence"] == "high"


def test_throttle_hot_series_with_collision_and_dax_names_collision_root_cause():
    chain = diagnose_throttle(_SERIES_HOT, _EVENTS_WITH_COLLISION)
    assert "collided" in chain["rootCause"].lower()
    dax_steps = [s for s in chain["chain"] if s["step"] == "dax anti-pattern"]
    assert len(dax_steps) == 1
    assert dax_steps[0]["verdict"] == "confirmed"
    assert dax_steps[0]["evidence"]["patterns"]
    assert chain["confidence"] == "high"


def test_throttle_has_real_cost_false_driver_step_never_confirmed():
    chain = diagnose_throttle(_SERIES_HOT, _EVENTS_WITH_COLLISION, has_real_cost=False)
    driver_step = next(s for s in chain["chain"] if s["step"] == "who drove the over-window?")
    assert driver_step["verdict"] == "unconfirmed"
    assert "unranked" in driver_step["evidence"]["note"]


def test_every_chain_entry_has_all_four_keys_throttle_branch():
    for chain in (diagnose_throttle(_SERIES_CALM, []),
                  diagnose_throttle(_SERIES_HOT, _EVENTS_WITH_COLLISION),
                  diagnose_throttle(_SERIES_HOT, _EVENTS_NO_COLLISION)):
        for entry in chain["chain"]:
            assert set(entry.keys()) == {"step", "hypothesis", "verdict", "evidence"}


def test_refresh_branch_no_failures_eliminates():
    refreshes = [{"workspace": "ws", "datasetName": "Sales", "status": "Success",
                  "startTime": "2026-07-07T09:00:00Z", "refreshAttempts": []}]
    chain = diagnose_refresh(refreshes, [], _SERIES_CALM)
    assert chain["chain"][0]["verdict"] == "eliminated"
    assert chain["rootCause"] is None


def test_refresh_branch_with_failure_and_retry_storm_names_error_class():
    refreshes = [{
        "workspace": "ws", "datasetName": "Sales", "status": "Failed",
        "startTime": "2026-07-07T09:00:00Z",
        "serviceExceptionJson": '{"errorCode": "TimeoutExpired"}',
        "refreshAttempts": [{"type": "Data", "startTime": "2026-07-07T09:00:00Z",
                              "endTime": "2026-07-07T09:01:00Z"}] * 4,
    }]
    chain = diagnose_refresh(refreshes, [], _SERIES_CALM)
    assert chain["rootCause"] is not None
    assert "timeout" in chain["rootCause"].lower()
    for entry in chain["chain"]:
        assert set(entry.keys()) == {"step", "hypothesis", "verdict", "evidence"}


def test_slowness_branch_not_throttling_hot_item_names_root_cause():
    events = ([{"ts": "2026-07-07T09:00:00Z", "user": "a@co", "item": "Sales",
                "kind": "interactive", "cuSeconds": 100.0}] * 4
              + [{"ts": "2026-07-07T09:00:00Z", "user": "b@co", "item": "Other",
                  "kind": "interactive", "cuSeconds": 10.0}])
    chain = diagnose_slowness(_SERIES_CALM, events)
    assert chain["rootCause"] is not None
    assert "sales" in chain["rootCause"].lower()


def test_slowness_branch_nothing_confirms_gives_no_root_cause_and_eliminated_list():
    events = [{"ts": "2026-07-07T09:00:00Z", "user": "a@co", "item": "Sales",
               "kind": "interactive", "cuSeconds": 10.0},
              {"ts": "2026-07-07T09:00:00Z", "user": "b@co", "item": "Other",
               "kind": "interactive", "cuSeconds": 10.0},
              {"ts": "2026-07-07T09:00:00Z", "user": "c@co", "item": "Third",
               "kind": "interactive", "cuSeconds": 10.0},
              {"ts": "2026-07-07T09:00:00Z", "user": "d@co", "item": "Fourth",
               "kind": "interactive", "cuSeconds": 10.0}]
    chain = diagnose_slowness(_SERIES_CALM, events)
    assert chain["rootCause"] is None
    assert chain["eliminated"]


def test_run_diagnosis_dispatches_and_bogus_symptom_raises():
    chain = run_diagnosis("throttle", series=_SERIES_CALM, events=[])
    assert chain["symptom"] == "throttle"
    with pytest.raises(ValueError):
        run_diagnosis("bogus", series=_SERIES_CALM, events=[])


def test_run_diagnosis_refresh_and_slowness_dispatch():
    refreshes = [{"workspace": "ws", "datasetName": "Sales", "status": "Success",
                  "startTime": "2026-07-07T09:00:00Z", "refreshAttempts": []}]
    chain = run_diagnosis("refresh", series=_SERIES_CALM, events=[], refreshes=refreshes)
    assert chain["symptom"] == "refresh"
    chain2 = run_diagnosis("slowness", series=_SERIES_CALM, events=[])
    assert chain2["symptom"] == "slowness"
