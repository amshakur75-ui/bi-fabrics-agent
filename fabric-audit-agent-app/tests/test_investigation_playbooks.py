# tests/test_investigation_playbooks.py
from fabric_audit_agent.investigation.playbooks import investigate_user
from fabric_audit_agent.adapters.reasoner_investigation import create_investigation_reasoner
from fabric_audit_agent.investigation.playbooks import investigate_capacity_spike


def _facts(users):
    return {"capacity": {"peakCuPct": 120.0, "throttleMinutes": 10},
            "items": [{"workspace": "Sales", "name": "A4A", "sharePct": 90, "attributionMode": "cost",
                       "topUsers": [{"user": "x@co", "cuSeconds": 900}], "userCount": 1}],
            "users": users}


def _collector(facts):
    return {"collect": lambda: facts}


def test_investigate_user_found_builds_grounded_result():
    facts = _facts([{"user": "x@co", "cuSeconds": 900, "sharePct": 90,
                     "topItems": [{"name": "A4A", "cuSeconds": 900}], "itemCount": 1}])
    out = investigate_user(_collector(facts), create_investigation_reasoner(), "x@co", days=30)
    assert out["abstained"] is False
    assert out["coverage"]["workspacesSeen"] == ["Sales"]
    assert any("A4A" in e["summary"] or "90" in str(e["data"]) for e in out["evidence"])
    assert "x@co" in out["result"]["explanation"]


def test_investigate_user_absent_abstains_not_hallucinates():
    facts = _facts([{"user": "someone@co", "cuSeconds": 5, "sharePct": 100, "topItems": [], "itemCount": 0}])
    out = investigate_user(_collector(facts), create_investigation_reasoner(), "ghost@co", days=30)
    assert out["abstained"] is True
    assert out["confidence"]["level"] == "insufficient"
    assert out["result"]["hypotheses"] == []     # never invents a cause for a user it can't see


def test_capacity_spike_names_top_driver_when_throttled():
    facts = _facts([{"user": "x@co", "cuSeconds": 900, "sharePct": 90, "topItems": [{"name": "A4A", "cuSeconds": 900}], "itemCount": 1}])
    out = investigate_capacity_spike(_collector(facts), create_investigation_reasoner())
    assert out["abstained"] is False
    assert any("120" in str(e["data"]) or "120" in e["summary"] for e in out["evidence"])  # peak CU%
    assert any("A4A" in e["summary"] for e in out["evidence"])                              # top item


def test_capacity_spike_abstains_without_capacity_signal():
    facts = {"items": [], "users": []}   # no capacity events wired
    out = investigate_capacity_spike(_collector(facts), create_investigation_reasoner())
    assert out["abstained"] is True and out["confidence"]["level"] == "insufficient"


# --- `when` window analysis (Phase-3 events wired into the spike playbook) ---

_WINDOW_EVENTS = [
    {"ts": "2026-07-06T15:40:00Z", "user": "refresher@co", "item": "SCM", "kind": "refresh",
     "cuSeconds": 900.0},
    {"ts": "2026-07-06T15:50:00Z", "user": "alice@co", "item": "Sales", "kind": "interactive",
     "cuSeconds": 100.0},
    {"ts": "2026-07-06T09:00:00Z", "user": "faraway@co", "item": "HR", "kind": "interactive",
     "cuSeconds": 5000.0},   # hours outside the window -- must be excluded
]
_WINDOW_SERIES = [
    {"ts": "2026-07-06T15:48:00Z", "cuPct": 184.7},
    {"ts": "2026-07-06T09:00:00Z", "cuPct": 55.0},   # outside window
]


def _spike_facts():
    return _facts([{"user": "x@co", "cuSeconds": 900, "sharePct": 90,
                    "topItems": [{"name": "A4A", "cuSeconds": 900}], "itemCount": 1}])


def _window_ev(out):
    return next(e for e in out["evidence"] if e["kind"] == "window")


def test_when_scopes_events_to_window_and_names_driver():
    out = investigate_capacity_spike(_collector(_spike_facts()), create_investigation_reasoner(),
                                     when="2026-07-06T15:48:00Z",
                                     events=_WINDOW_EVENTS, capacity_series=_WINDOW_SERIES)
    ev = _window_ev(out)
    d = ev["data"]
    assert d["eventCount"] == 2                      # the 09:00 event is excluded
    assert d["refreshCuSeconds"] == 900.0
    assert d["interactiveCuSeconds"] == 100.0
    assert d["driver"] == "refresh-driven"           # answers refresh-vs-interactive for THE peak
    assert d["windowPeakCuPct"] == 184.7
    assert d["topEvents"][0]["user"] == "refresher@co"
    assert "faraway@co" not in str(d)


def test_when_accepts_display_format():
    out = investigate_capacity_spike(_collector(_spike_facts()), create_investigation_reasoner(),
                                     when="2026-07-06 15:48 UTC (11:48 AM EDT)",
                                     events=_WINDOW_EVENTS, capacity_series=_WINDOW_SERIES)
    assert _window_ev(out)["data"]["eventCount"] == 2


def test_when_with_no_events_in_window_is_honest():
    out = investigate_capacity_spike(_collector(_spike_facts()), create_investigation_reasoner(),
                                     when="2026-07-01T03:00:00Z",
                                     events=_WINDOW_EVENTS, capacity_series=_WINDOW_SERIES)
    ev = _window_ev(out)
    assert ev["data"]["eventCount"] == 0
    assert "no telemetry events" in ev["summary"]    # says it can't attribute, not a guess


def test_when_unparseable_is_flagged_not_crashed():
    out = investigate_capacity_spike(_collector(_spike_facts()), create_investigation_reasoner(),
                                     when="yesterday-ish", events=_WINDOW_EVENTS)
    assert "could not parse" in _window_ev(out)["summary"]


def test_window_corroboration_raises_confidence():
    base = investigate_capacity_spike(_collector(_spike_facts()), create_investigation_reasoner())
    scoped = investigate_capacity_spike(_collector(_spike_facts()), create_investigation_reasoner(),
                                        when="2026-07-06T15:48:00Z", events=_WINDOW_EVENTS)
    assert base["confidence"]["level"] == "high"     # already 2 sources
    assert scoped["confidence"]["level"] == "high"
    assert "3 sources" in scoped["confidence"]["basis"]   # window telemetry adds a third


def test_no_when_keeps_prior_behavior():
    out = investigate_capacity_spike(_collector(_spike_facts()), create_investigation_reasoner())
    assert not [e for e in out["evidence"] if e["kind"] == "window"]


def test_window_truncation_is_disclosed():
    out = investigate_capacity_spike(_collector(_spike_facts()), create_investigation_reasoner(),
                                     when="2026-07-06T15:48:00Z",
                                     events=_WINDOW_EVENTS, capacity_series=_WINDOW_SERIES,
                                     events_truncated=True)
    ev = _window_ev(out)
    assert ev["data"]["eventsTruncated"] is True
    assert "cap hit" in ev["summary"]              # summary discloses the partial-slice caveat


def test_window_not_truncated_has_no_flag():
    out = investigate_capacity_spike(_collector(_spike_facts()), create_investigation_reasoner(),
                                     when="2026-07-06T15:48:00Z", events=_WINDOW_EVENTS)
    ev = _window_ev(out)
    assert "eventsTruncated" not in ev["data"]     # absence means the window was fully covered
    assert "cap hit" not in ev["summary"]


# --- Group 2: baseline wiring ---

def test_investigate_user_uses_baseline_when_history_present():
    """When history rows exist for the user and today is an outlier, a baseline evidence item
    with kind=='baseline' must be present and its summary must mention 'ABOVE p95'."""
    history_rows = [{"cuSeconds": c} for c in (10, 20, 30, 40, 50)]
    facts = _facts([{"user": "x@co", "cuSeconds": 500, "sharePct": 99,
                     "topItems": [{"name": "A4A", "cuSeconds": 500}], "itemCount": 1}])
    facts["history"] = {"x@co": history_rows}
    # Override user cuSeconds to an outlier value
    facts["users"][0]["cuSeconds"] = 500
    out = investigate_user(_collector(facts), create_investigation_reasoner(), "x@co", days=30)
    baseline_items = [e for e in out["evidence"] if e["kind"] == "baseline"]
    assert len(baseline_items) == 1, "expected exactly one baseline evidence item"
    assert "ABOVE p95" in baseline_items[0]["summary"]


def test_investigate_user_no_baseline_item_without_history():
    """When no history is present, no baseline evidence item should appear (no fabrication)."""
    facts = _facts([{"user": "x@co", "cuSeconds": 900, "sharePct": 90,
                     "topItems": [{"name": "A4A", "cuSeconds": 900}], "itemCount": 1}])
    # No 'history' key in facts
    out = investigate_user(_collector(facts), create_investigation_reasoner(), "x@co", days=30)
    baseline_items = [e for e in out["evidence"] if e["kind"] == "baseline"]
    assert len(baseline_items) == 0, "must not fabricate a baseline when history is absent"


def test_investigate_user_baseline_uses_days_window():
    """The baseline evidence summary must reference the `days` value passed to investigate_user."""
    history_rows = [{"cuSeconds": c} for c in (10, 20, 30, 40, 50)]
    facts = _facts([{"user": "x@co", "cuSeconds": 500, "sharePct": 99,
                     "topItems": [{"name": "A4A", "cuSeconds": 500}], "itemCount": 1}])
    facts["history"] = {"x@co": history_rows}
    facts["users"][0]["cuSeconds"] = 500
    out = investigate_user(_collector(facts), create_investigation_reasoner(), "x@co", days=7)
    baseline_items = [e for e in out["evidence"] if e["kind"] == "baseline"]
    assert len(baseline_items) == 1, "expected exactly one baseline evidence item"
    # The summary should mention the days value passed
    assert "7" in baseline_items[0]["summary"]
