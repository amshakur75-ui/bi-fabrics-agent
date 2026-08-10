"""B4 (Design A' Phase B) — capacity_reporting Delta table populated on every sweep.

Long-tail archive of what each 5-min sweep saw. Covers:
  - The pure ``_extract_from_facts`` shape (all capacity fields hoisted, item count, signal types)
  - Memory-store contract (append / recent / query_range)
  - camelCase <-> snake_case + JSON-encoded signalTypes lossless roundtrip
  - Wire-in to run_tier2_check: one row appended per sweep, best-effort on failure
"""
from datetime import datetime, timezone

from fabric_audit_agent.context_capacity_reporting import (
    _extract_from_facts, _to_row, _from_row,
    create_capacity_reporting_store_memory,
)
from fabric_audit_agent.automation.tier2_check import run_tier2_check
from fabric_audit_agent.context_alerts import create_alerts_store_memory


T0 = datetime(2026, 8, 5, 13, 52, 0, tzinfo=timezone.utc)


def _facts(**cap_overrides):
    """A representative sweep facts dict with every field populated so the reporting row is
    fully rich. Individual tests override specific pieces via kwargs."""
    cap = {
        "peakCuPct": 210.0, "peakAt": "2026-08-05T13:52:00Z",
        "throttleMinutes": 8.5, "capacityId": "cap-A",
        "overageTotalMs": 12000.0, "overageCumulativePct": 12.5, "minutesToBurndown": 0.06,
        "maxInteractiveDelayPct": 88.0, "maxInteractiveRejectionPct": 40.0,
        "maxBackgroundRejectionPct": 30.0,
    }
    cap.update(cap_overrides)
    return {"capacity": cap, "items": [{"name": "R1"}, {"name": "R2"}]}


def test_extract_hoists_every_field_from_facts():
    row = _extract_from_facts(_facts(), run_at="2026-08-05T13:52:05Z",
                                signal_types=["throttle", "pressure"], collector_ok=True)
    assert row["runAt"] == "2026-08-05T13:52:05Z"
    assert row["capacityId"] == "cap-A"
    assert row["peakCuPct"] == 210.0 and row["peakAt"] == "2026-08-05T13:52:00Z"
    assert row["throttleMinutes"] == 8.5
    assert row["overageTotalMs"] == 12000.0 and row["overageCumulativePct"] == 12.5
    assert row["minutesToBurndown"] == 0.06
    assert row["maxInteractiveDelayPct"] == 88.0
    assert row["maxInteractiveRejectionPct"] == 40.0
    assert row["maxBackgroundRejectionPct"] == 30.0
    assert row["itemCount"] == 2                        # attribution coverage
    assert row["signalTypes"] == ["throttle", "pressure"]
    assert row["collectorOk"] is True


def test_extract_handles_missing_capacity_gracefully():
    """A collector that returned no capacity data (blind sweep) still produces a reporting
    row — with None for the metrics + itemCount=0 — so the archive shows the miss too."""
    row = _extract_from_facts({"capacity": {}, "items": []},
                                run_at="2026-08-05T13:52:00Z",
                                signal_types=[], collector_ok=False)
    assert row["peakCuPct"] is None and row["throttleMinutes"] is None
    assert row["itemCount"] == 0 and row["signalTypes"] == []
    assert row["collectorOk"] is False


def test_extract_distinguishes_none_signal_types_from_empty_list():
    """None means "we didn't compute this yet" (e.g. an old row before B4); [] means
    "sweep succeeded, no check fired". Analysts need to tell them apart."""
    row_none = _extract_from_facts(_facts(), run_at="t", signal_types=None)
    row_empty = _extract_from_facts(_facts(), run_at="t", signal_types=[])
    assert row_none["signalTypes"] is None
    assert row_empty["signalTypes"] == []


def test_row_roundtrip_is_lossless_including_json_signaltypes():
    """Every column round-trips: camelCase dict -> snake_case row -> camelCase dict. The
    signalTypes JSON encode/decode must not lose the list identity."""
    original = _extract_from_facts(_facts(), run_at="2026-08-05T13:52:05Z",
                                     signal_types=["throttle", "capacity_incident"],
                                     collector_ok=True)
    round_tripped = _from_row(_to_row(original))
    assert round_tripped == original                    # equal, including list order


def test_row_signaltypes_empty_list_survives_roundtrip():
    """Empty list is a MEANINGFUL value (sweep ran, nothing fired) — serialized as ``"[]"``
    on the Delta side and decoded back to an empty list, not None."""
    row = _extract_from_facts(_facts(), run_at="t", signal_types=[], collector_ok=True)
    assert _from_row(_to_row(row))["signalTypes"] == []


def test_memory_store_append_and_recent():
    """Append-only + newest-first snapshot. Sanity check on the memory adapter."""
    store = create_capacity_reporting_store_memory()
    for i in range(5):
        store["append"](_extract_from_facts(_facts(), run_at=f"2026-08-05T13:5{i}:00Z",
                                             signal_types=[]))
    recent = store["recent"](3)
    assert len(recent) == 3
    # newest-first ordering (54, 53, 52)
    stamps = [r["runAt"] for r in recent]
    assert stamps == ["2026-08-05T13:54:00Z", "2026-08-05T13:53:00Z",
                       "2026-08-05T13:52:00Z"]


def test_memory_store_query_range_filters_inclusively():
    store = create_capacity_reporting_store_memory()
    for i in range(0, 60, 10):
        store["append"](_extract_from_facts(_facts(),
                                             run_at=f"2026-08-05T14:{i:02d}:00Z",
                                             signal_types=[]))
    got = store["query_range"]("2026-08-05T14:20:00Z", "2026-08-05T14:40:00Z")
    stamps = sorted(r["runAt"] for r in got)
    assert stamps == ["2026-08-05T14:20:00Z", "2026-08-05T14:30:00Z",
                       "2026-08-05T14:40:00Z"]


def test_memory_store_seeds_from_initial_rows():
    seed = [_extract_from_facts(_facts(), run_at="2026-08-04T00:00:00Z", signal_types=[])]
    store = create_capacity_reporting_store_memory(seed)
    assert len(store["recent"](10)) == 1
    assert store["recent"](10)[0]["runAt"] == "2026-08-04T00:00:00Z"


def test_run_tier2_check_appends_a_reporting_row_when_store_provided():
    """The wire-in: a full sweep with reporting_store threaded in leaves exactly one archival
    row behind, carrying the capacity snapshot + the list of tier2 checks that fired."""
    facts = _facts()
    collector = {"collect": lambda: facts}
    alerts_store = create_alerts_store_memory()
    reporting_store = create_capacity_reporting_store_memory()

    res = run_tier2_check(collector, delivery_sinks=None, alerts_store=alerts_store,
                          now_dt=T0, config={}, reporting_store=reporting_store)
    assert res["triggered"] is True

    rows = reporting_store["recent"](10)
    assert len(rows) == 1
    r = rows[0]
    # capacity snapshot fully hoisted
    assert r["peakCuPct"] == 210.0 and r["throttleMinutes"] == 8.5
    assert r["capacityId"] == "cap-A"
    # signalTypes records the RAW component checks as detected, NOT the delivery-layer
    # composite. "throttle + pressure fired at 13:52" is the queryable analytics fact;
    # `capacity_incident` is a Teams-card concept and deliberately never lands here.
    assert set(r["signalTypes"]).issuperset({"throttle", "pressure"})
    assert "capacity_incident" not in r["signalTypes"]


def test_run_tier2_check_appends_no_row_when_reporting_store_absent():
    """Backwards-compat: existing call sites with no reporting_store must behave EXACTLY as
    before — no archival side effect."""
    facts = _facts()
    collector = {"collect": lambda: facts}
    alerts_store = create_alerts_store_memory()

    # No reporting_store => the sweep runs normally, no attempt to write, no error.
    res = run_tier2_check(collector, delivery_sinks=None, alerts_store=alerts_store,
                          now_dt=T0, config={})
    assert res["triggered"] is True


def test_run_tier2_check_isolated_when_reporting_store_raises():
    """A misbehaving store must NOT crash the sweep — the archival write is best-effort.
    Triggers still fire; the sweep result is unchanged; only the archive row is skipped."""
    class BadStore:
        def __init__(self):
            self.calls = 0

        def append(self, r):
            self.calls += 1
            raise RuntimeError("delta write failed")

    bad = BadStore()
    facts = _facts()
    collector = {"collect": lambda: facts}
    alerts_store = create_alerts_store_memory()
    reporting_store = {"append": bad.append,
                        "recent": lambda n=100: [],
                        "query_range": lambda s, e: []}

    res = run_tier2_check(collector, delivery_sinks=None, alerts_store=alerts_store,
                          now_dt=T0, config={}, reporting_store=reporting_store)
    assert res["triggered"] is True                     # sweep succeeded
    assert bad.calls == 1                               # we attempted the write once


def test_run_tier2_check_records_collector_blindness_in_the_reporting_row():
    """A blind sweep (collector returned nothing) still gets an archival row — with
    collectorOk=False, signalTypes empty (no capacity checks could fire), and metrics NULL.
    The archive is exactly the surface an ops review looks at to spot silent visibility gaps."""
    collector = {"collect": lambda: {}}                 # empty payload = blind
    alerts_store = create_alerts_store_memory()
    reporting_store = create_capacity_reporting_store_memory()

    run_tier2_check(collector, delivery_sinks=None, alerts_store=alerts_store,
                    now_dt=T0, config={}, reporting_store=reporting_store)
    rows = reporting_store["recent"](5)
    assert len(rows) == 1
    r = rows[0]
    assert r["collectorOk"] is False
    assert r["peakCuPct"] is None and r["throttleMinutes"] is None
    assert r["itemCount"] == 0
