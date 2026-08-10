"""Structural guard across EVERY Delta-backed store: what the code writes must be mappable.

THE BUG CLASS THIS PREVENTS
---------------------------
Each store keeps a ``_FIELDS`` list of (camelCaseKey, snake_case_column) pairs. ``_to_row``
builds the Delta row by iterating that list, so **a key not listed there is silently discarded
on write and reads back as None**. The in-memory doubles used throughout the suite keep the whole
dict, so a missing mapping is invisible to every test that uses them.

On 2026-08-10 that gap shipped three unmapped fields on ``audit_alerts`` with a fully green
2106-test suite; production behaviour diverged completely (8 Teams cards vs 2, incidents that
never resolved). ``tests/test_alerts_store_delta_fidelity.py`` pins the behaviour for that one
store. This file generalizes the STRUCTURAL half to all of them, so the next store to grow a
field is covered without anyone remembering to add a test.
"""
import inspect

import pytest

from fabric_audit_agent import context_alerts, context_readings, context_user_baseline
from fabric_audit_agent import context_capacity_reporting
from fabric_audit_agent.automation.user_baseline_bootstrap import build_baselines
from fabric_audit_agent.adapters.collector_baseline_la import create_baseline_collector

ALL_STORES = [context_alerts, context_readings, context_user_baseline,
              context_capacity_reporting]


@pytest.mark.parametrize("mod", ALL_STORES, ids=lambda m: m.__name__.split(".")[-1])
def test_every_store_has_a_consistent_field_mapping(mod):
    """_FIELDS must be pairs of distinct camelCase keys and distinct snake_case columns."""
    fields = mod._FIELDS
    cc = [c for c, _ in fields]
    cols = [c for _, c in fields]
    assert len(cc) == len(set(cc)), f"duplicate camelCase key in {mod.__name__}._FIELDS"
    assert len(cols) == len(set(cols)), f"duplicate column in {mod.__name__}._FIELDS"
    for key, col in fields:
        assert key and col, f"empty mapping entry in {mod.__name__}"
        assert "_" not in key, f"{mod.__name__}: '{key}' should be camelCase"
        assert col.islower(), f"{mod.__name__}: column '{col}' should be snake_case"


@pytest.mark.parametrize("mod", ALL_STORES, ids=lambda m: m.__name__.split(".")[-1])
def test_to_row_from_row_round_trip_is_lossless(mod):
    """Populate EVERY mapped field with a sentinel and assert it survives the round trip.

    A field present in _FIELDS but mishandled by a JSON encode/decode (or dropped by a typo in
    one of the two mappers) fails here.
    """
    sentinel = {}
    for i, (key, _) in enumerate(mod._FIELDS):
        # signalTypes-style list fields must round-trip as lists; everything else as a scalar.
        json_list = set(getattr(mod, "_JSON_LIST_FIELDS", ()))
        sentinel[key] = ["a", "b"] if key in json_list or key == "signalTypes" else f"v{i}"
    assert mod._from_row(mod._to_row(sentinel)) == sentinel


@pytest.mark.parametrize("mod", ALL_STORES, ids=lambda m: m.__name__.split(".")[-1])
def test_schema_and_sql_types_cover_every_column(mod):
    """If the module declares per-column SQL types for its self-heal ALTER, every key in that
    map must be a real column — a typo there silently means the ALTER adds a STRING column with
    the wrong type (or, worse, the map entry is dead and the real column defaults to STRING)."""
    cols = {c for _, c in mod._FIELDS}
    for attr in ("_COL_SQL_TYPE",):
        m = getattr(mod, attr, None)
        if m is None:
            # The alerts store defines it inside the factory; skip when not module-level.
            continue
        unknown = sorted(set(m) - cols)
        assert unknown == [], f"{mod.__name__}.{attr} has non-columns: {unknown}"


def test_alerts_col_sql_type_map_is_consistent_with_fields():
    """context_alerts declares _COL_SQL_TYPE inside create_alerts_store_delta; pull it out of the
    source so a typo there still gets caught."""
    src = inspect.getsource(context_alerts.create_alerts_store_delta)
    cols = {c for _, c in context_alerts._FIELDS}
    # Every quoted key in the _COL_SQL_TYPE literal must be a real column.
    import re
    block = src.split("_COL_SQL_TYPE = {", 1)[1].split("}", 1)[0]
    declared = set(re.findall(r'"([a-z_]+)":', block))
    assert declared, "could not parse _COL_SQL_TYPE"
    assert declared <= cols, f"unknown columns in _COL_SQL_TYPE: {sorted(declared - cols)}"


# --- producer/consumer agreement --------------------------------------------------------

def test_baseline_producers_emit_only_mappable_keys():
    """Both baseline producers (the Python reduction and the KQL aggregate collector) must emit
    exactly the keys context_user_baseline can persist. A producer key outside _FIELDS is
    silently dropped on the Delta write."""
    mappable = {k for k, _ in context_user_baseline._FIELDS}

    reduced = build_baselines(
        [{"user": "a@x", "cuSeconds": float(i)} for i in range(30)],
        min_history=20, as_of="t")
    assert reduced, "fixture should produce rows"
    for row in reduced:
        assert set(row) <= mappable, f"build_baselines emits unmappable {set(row) - mappable}"

    def fake_query(kql):
        if "by _euser" in kql:
            return [{"user": "a@x", "p50": 1.0, "p95": 2.0, "sampleCount": 99,
                     "minCu": 0.1, "maxCu": 9.0}]
        return [{"p50": 1.0, "p95": 2.0, "sampleCount": 500, "minCu": 0.1, "maxCu": 9.0}]

    agg = create_baseline_collector(fake_query, {"minHistory": 20, "asOf": "t"})["collect"]()
    assert agg, "aggregate collector should produce rows"
    for row in agg:
        assert set(row) <= mappable, f"aggregate emits unmappable {set(row) - mappable}"


def test_capacity_reporting_extractor_emits_only_mappable_keys():
    row = context_capacity_reporting._extract_from_facts(
        {"capacity": {"peakCuPct": 1.0, "throttleMinutes": 2.0, "capacityId": "c",
                      "peakAt": "t", "overageTotalMs": 3.0, "overageCumulativePct": 4.0,
                      "minutesToBurndown": 5.0, "maxInteractiveDelayPct": 6.0,
                      "maxInteractiveRejectionPct": 7.0, "maxBackgroundRejectionPct": 8.0},
         "items": [{"name": "x"}]},
        run_at="t", signal_types=["throttle"], collector_ok=True)
    mappable = {k for k, _ in context_capacity_reporting._FIELDS}
    assert set(row) <= mappable, f"unmappable keys: {set(row) - mappable}"
    # ...and it fills EVERY mappable key, so a new column can't be silently left NULL forever.
    assert set(row) == mappable


def test_readings_producer_emits_only_mappable_keys():
    """tier2_check._record_reading builds the readings row inline; keep it in agreement."""
    from fabric_audit_agent.automation.tier2_check import _record_reading
    captured = {}
    store = {"append": lambda r: captured.update(r), "recent": lambda n=12: []}
    _record_reading(store, run_at="t",
                    facts={"capacity": {"peakCuPct": 1.0, "throttleMinutes": 2.0},
                           "items": [{"name": "x"}]},
                    collector_ok=True)
    mappable = {k for k, _ in context_readings._FIELDS}
    assert set(captured) <= mappable, f"unmappable keys: {set(captured) - mappable}"
