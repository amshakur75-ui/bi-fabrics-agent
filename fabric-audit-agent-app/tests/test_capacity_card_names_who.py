"""Round 4: the capacity card must name WHO, and a share must have a denominator worth dividing.

Three product-level defects, all of the "runs fine, answers the wrong question" kind:

  * P4 of the product promise is "the card names who caused it". It did not. `_facts_for`'s
    capacity branches read only the capacity metrics and never `facts["items"]` / `topUsers`, even
    though the Log Analytics attribution collector populates them on EVERY sweep. A 210% peak with
    one heavy user active produced a card of percentages with no name; the name reached a human only
    via a separate concentration ticket needing 3 consecutive ticks (~15 min), which never goes to
    Teams — by which time a short incident is over.
  * `sharePct` is a share of a FIVE-MINUTE denominator, so one overnight refresh in an idle window
    is ~100% of ~nothing. The "30% concentration alert" fired on a near-empty denominator by
    construction.
  * `_check_overage` never copied `peakCuPct`, and `primary_metric` returns `peakCuPct` for the
    whole capacity family — so an overage-only incident stored `metric=None`, permanently disabling
    its peak-escalation axis and showing no utilisation figure anywhere.
"""
from fabric_audit_agent.automation.materiality import load_cfg
from fabric_audit_agent.automation.tier2_check import (
    _check_concentration, _check_overage, _coalesce_capacity_family, _facts_for, _likely_drivers)
from fabric_audit_agent.automation.incident import primary_metric


def _item(name, ws, share, cu, users=None):
    return {"name": name, "workspace": ws, "sharePct": share, "cuSeconds": cu,
            "topUsers": users or [], "attributionMode": "cost-cpu"}


def _facts(peak=210.0, items=None, **cap):
    c = {"peakCuPct": peak, "throttleMinutes": 8.5, "capacityId": "cap-1"}
    c.update(cap)
    return {"capacity": c, "items": items if items is not None else []}


# ---- WHO ------------------------------------------------------------------

def test_likely_drivers_ranks_items_by_monitored_cost_and_names_the_top_user():
    facts = _facts(items=[
        _item("Sales", "Ent", 20.0, 500.0, [{"user": "alice@co", "cuSeconds": 400.0}]),
        _item("DTC", "Ent", 60.0, 3000.0, [{"user": "bob@co", "cuSeconds": 100.0},
                                           {"user": "carol@co", "cuSeconds": 2500.0}]),
    ])
    drivers = _likely_drivers(facts)
    assert [d["item"] for d in drivers] == ["DTC", "Sales"]        # by cost, heaviest first
    assert drivers[0]["user"] == "carol@co"                        # the item's HEAVIEST user
    assert drivers[0]["cuSeconds"] == 3000.0


def test_items_with_no_cost_are_dropped_rather_than_ranked_arbitrarily():
    """An arbitrary "likely driver" is worse than naming nobody — it points a human at the wrong
    person with the same confidence as a real answer."""
    facts = _facts(items=[_item("NoCost", "Ent", 90.0, None),
                          _item("Real", "Ent", 10.0, 50.0)])
    assert [d["item"] for d in _likely_drivers(facts)] == ["Real"]


def test_a_capacity_incident_card_names_the_driver():
    from fabric_audit_agent.automation.tier2_check import _check_throttle, _check_pressure
    facts = _facts(items=[_item("DTC", "Ent", 60.0, 3000.0,
                                [{"user": "carol@co", "cuSeconds": 2500.0}])])
    trigs = _check_throttle(facts) + _check_pressure(facts)
    composite = _coalesce_capacity_family(trigs)[0]
    assert composite["check"] == "capacity_incident"
    blob = str(_facts_for(composite))
    assert "carol@co" in blob and "DTC" in blob


def test_a_lone_capacity_signal_also_names_the_driver():
    """Composites return early from _facts_for; a single-signal window takes the shared path and
    must not be a blind spot."""
    from fabric_audit_agent.automation.tier2_check import _check_throttle
    facts = _facts(items=[_item("DTC", "Ent", 60.0, 3000.0,
                                [{"user": "carol@co", "cuSeconds": 2500.0}])])
    (trig,) = _check_throttle(facts)
    assert "carol@co" in str(_facts_for(trig))


def test_the_driver_row_never_calls_the_proxy_billed_capacity_cu():
    """CpuTimeMs/DurationMs attribution is a PROXY. Presenting it as capacity CU is the claim
    gates.true_cu_per_user_gate marks permanently blocked."""
    from fabric_audit_agent.automation.tier2_check import _check_throttle
    facts = _facts(items=[_item("DTC", "Ent", 60.0, 3000.0,
                                [{"user": "carol@co", "cuSeconds": 2500.0}])])
    (trig,) = _check_throttle(facts)
    label = [n for n, _v in _facts_for(trig) if "driver" in n.lower()][0]
    assert "monitored" in label.lower()
    assert "not billed" in label.lower()


def test_no_driver_row_when_there_is_no_attribution():
    from fabric_audit_agent.automation.tier2_check import _check_throttle
    (trig,) = _check_throttle(_facts(items=[]))
    assert not any("driver" in n.lower() for n, _v in _facts_for(trig))


# ---- the denominator ------------------------------------------------------

def test_an_idle_window_does_not_produce_a_concentration_alert():
    """One small refresh alone in a 5-minute window is ~100% share of almost no work."""
    facts = {"capacity": {"peakCuPct": 8.0},
             "items": [_item("Nightly", "Ent", 100.0, 3.0)]}      # 3 CU-s total, floor is 60
    assert _check_concentration(facts) == []


def test_a_busy_window_still_produces_the_alert():
    facts = {"capacity": {"peakCuPct": 88.0},
             # Sales at 24% is under the 30% gate, so exactly one item should trigger.
             "items": [_item("DTC", "Ent", 62.0, 4000.0), _item("Sales", "Ent", 24.0, 1500.0)]}
    trigs = _check_concentration(facts)
    assert len(trigs) == 1 and trigs[0]["item"] == "DTC"


def test_an_unmeasurable_window_is_not_silenced_by_the_floor():
    """`measured` is load-bearing. If NO item reports a cost — frequency-mode attribution, or a cost
    column that failed to resolve — applying the floor would convert a DATA GAP into silence, which
    is the same failure as reporting peakCuPct 0 for an unparseable export: the most reassuring
    possible answer, from no evidence."""
    facts = {"capacity": {"peakCuPct": 88.0},
             "items": [{"name": "DTC", "workspace": "Ent", "sharePct": 62.0}]}   # no cuSeconds
    assert len(_check_concentration(facts)) == 1


def test_the_floor_is_configurable_and_zero_disables_it(monkeypatch):
    monkeypatch.setenv("FABRIC_TIER2_MIN_WINDOW_CU", "0")
    assert load_cfg()["min_window_cu"] == 0.0
    facts = {"capacity": {"peakCuPct": 8.0}, "items": [_item("Nightly", "Ent", 100.0, 3.0)]}
    assert len(_check_concentration(facts)) == 1     # floor off -> the old behaviour is available


# ---- the overage metric ---------------------------------------------------

def test_an_overage_only_incident_carries_a_comparable_metric():
    """primary_metric returns peakCuPct for the WHOLE capacity family, so an overage trigger
    without it stored metric=None — the peak-escalation axis was dead for that incident forever."""
    facts = _facts(peak=118.0, overageTotalMs=84600000.0, minutesToBurndown=50.0,
                   overageCumulativePct=12.0)
    facts["capacity"]["throttleMinutes"] = 0.0
    (trig,) = _check_overage(facts)
    assert trig["peakCuPct"] == 118.0
    assert primary_metric(trig) == 118.0
