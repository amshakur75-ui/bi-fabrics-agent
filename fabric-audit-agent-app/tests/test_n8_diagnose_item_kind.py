"""N8 regression: diagnose_slowness must exclude system-item events from hot-item / hot-user
share computation when ``system_item_names`` is provided.

Without the exclusion, an EventStream item appearing in events at high CU can trivially
trigger the "single hot item" or "hot user surge" verdict -- a structural false positive,
not a real finding.
"""
from fabric_audit_agent.investigation.diagnose import diagnose_slowness, run_diagnosis


# A calm series (no throttling) so we reach the hot-item / hot-user steps.
_SERIES_CALM = [{"ts": f"2026-07-07T09:{m:02d}:00Z", "cuPct": 60.0} for m in range(10)]


# ---------------------------------------------------------------------------
# Core: system-item events are excluded from hot-item computation
# ---------------------------------------------------------------------------

def test_system_item_excluded_from_hot_item_check():
    """An event whose item name is in system_item_names must NOT contribute to the hot-item
    share, preventing a structural false positive."""
    events = [
        # System item: would be 80% of total without exclusion
        {"item": "Monitoring_ES", "user": "svc@internal", "cuSeconds": 80.0},
        {"item": "Sales Model", "user": "alice@co", "cuSeconds": 15.0},
        {"item": "HR Report", "user": "bob@co", "cuSeconds": 5.0},
    ]
    system_names = {"monitoring_es"}

    result = diagnose_slowness(_SERIES_CALM, events, system_item_names=system_names)

    # Without exclusion, "Monitoring_ES" at 80% share would trigger hot-item confirmed.
    # With exclusion, only Sales Model (75%) and HR Report (25%) remain -- Sales Model is
    # the hot item at 75% share, which IS over the 30% threshold. But the point is that
    # "Monitoring_ES" is not the one flagged.
    hot_step = next(s for s in result["chain"]
                    if s["hypothesis"] == "One item dominates workload share")
    if hot_step["verdict"] == "confirmed":
        assert hot_step["evidence"]["item"] != "Monitoring_ES", (
            "N8 regression: system item appeared as hot item despite exclusion"
        )


def test_system_item_excluded_prevents_false_hot_item():
    """When the ONLY dominant item is a system item, removing it should eliminate the
    hot-item step (no single real item dominates)."""
    events = [
        # System item dominates
        {"item": "Monitoring_ES", "user": "svc@internal", "cuSeconds": 100.0},
        # Real items are evenly split -> no single one dominates
        {"item": "Sales Model", "user": "alice@co", "cuSeconds": 10.0},
        {"item": "HR Report", "user": "bob@co", "cuSeconds": 10.0},
        {"item": "Finance Model", "user": "carol@co", "cuSeconds": 10.0},
        {"item": "Ops Dashboard", "user": "dave@co", "cuSeconds": 10.0},
    ]
    system_names = {"monitoring_es"}

    # Without system_item_names: Monitoring_ES at ~71% => hot-item confirmed
    result_without = diagnose_slowness(_SERIES_CALM, events)
    hot_without = next(s for s in result_without["chain"]
                       if s["hypothesis"] == "One item dominates workload share")
    assert hot_without["verdict"] == "confirmed"
    assert hot_without["evidence"]["item"] == "Monitoring_ES"

    # With system_item_names: each real item is 25% => none over 30% => hot-item eliminated
    result_with = diagnose_slowness(_SERIES_CALM, events, system_item_names=system_names)
    hot_with = next(s for s in result_with["chain"]
                    if s["hypothesis"] == "One item dominates workload share")
    assert hot_with["verdict"] == "eliminated", (
        "N8 regression: system-item exclusion didn't prevent false hot-item verdict"
    )


# ---------------------------------------------------------------------------
# System-item exclusion also applies to hot-user computation
# ---------------------------------------------------------------------------

def test_system_item_excluded_from_hot_user_check():
    """Events from system items should also be excluded from hot-user share, so a service
    account running EventStream events doesn't trigger a hot-user-surge false positive."""
    # Spread items so no single item dominates (avoids hot-item short-circuit).
    events = [
        # System item events from svc account -- would make svc the "hot user"
        {"item": "ES-1", "user": "svc@internal", "cuSeconds": 25.0},
        {"item": "ES-2", "user": "svc@internal", "cuSeconds": 25.0},
        {"item": "ES-3", "user": "svc@internal", "cuSeconds": 25.0},
        {"item": "ES-4", "user": "svc@internal", "cuSeconds": 25.0},
        # Real items from real users
        {"item": "Sales", "user": "alice@co", "cuSeconds": 10.0},
        {"item": "HR", "user": "bob@co", "cuSeconds": 10.0},
        {"item": "Finance", "user": "carol@co", "cuSeconds": 10.0},
        {"item": "Ops", "user": "dave@co", "cuSeconds": 10.0},
    ]
    system_names = {"es-1", "es-2", "es-3", "es-4"}

    # Without exclusion: svc@internal has 100 CU out of 140 total => ~71% => hot user confirmed
    result_without = diagnose_slowness(_SERIES_CALM, events)
    # Find the hot-user step (may or may not exist if hot-item fires first)
    user_steps_without = [s for s in result_without["chain"]
                          if s["hypothesis"] == "One user dominates workload share"]
    # With exclusion: only real events remain (40 CU), split 4 ways => 25% each => no hot user
    result_with = diagnose_slowness(_SERIES_CALM, events, system_item_names=system_names)
    user_steps_with = [s for s in result_with["chain"]
                       if s["hypothesis"] == "One user dominates workload share"]
    if user_steps_with:
        assert user_steps_with[0]["verdict"] == "eliminated"


# ---------------------------------------------------------------------------
# Backward compatibility: no system_item_names -> unchanged behavior
# ---------------------------------------------------------------------------

def test_no_system_item_names_behavior_unchanged():
    """Without system_item_names (the default), all events contribute -- backward compat."""
    events = [
        {"item": "Sales", "user": "alice@co", "cuSeconds": 80.0},
        {"item": "Other", "user": "bob@co", "cuSeconds": 20.0},
    ]
    result = diagnose_slowness(_SERIES_CALM, events)
    hot_step = next(s for s in result["chain"]
                    if s["hypothesis"] == "One item dominates workload share")
    assert hot_step["verdict"] == "confirmed"
    assert hot_step["evidence"]["item"] == "Sales"


def test_empty_system_item_names_behaves_like_none():
    """Passing an empty set is the same as None -- no exclusion."""
    events = [
        {"item": "Sales", "user": "alice@co", "cuSeconds": 80.0},
        {"item": "Other", "user": "bob@co", "cuSeconds": 20.0},
    ]
    result = diagnose_slowness(_SERIES_CALM, events, system_item_names=set())
    hot_step = next(s for s in result["chain"]
                    if s["hypothesis"] == "One item dominates workload share")
    assert hot_step["verdict"] == "confirmed"


# ---------------------------------------------------------------------------
# System-item exclusion doesn't prevent a real hot item from being detected
# ---------------------------------------------------------------------------

def test_real_hot_item_still_detected_alongside_excluded_system_item():
    """A real item that genuinely dominates should still fire, even when system items
    are excluded from the computation."""
    events = [
        {"item": "Monitoring_ES", "user": "svc@internal", "cuSeconds": 50.0},
        {"item": "Sales Model", "user": "alice@co", "cuSeconds": 90.0},
        {"item": "HR Report", "user": "bob@co", "cuSeconds": 10.0},
    ]
    system_names = {"monitoring_es"}

    result = diagnose_slowness(_SERIES_CALM, events, system_item_names=system_names)
    hot_step = next(s for s in result["chain"]
                    if s["hypothesis"] == "One item dominates workload share")
    assert hot_step["verdict"] == "confirmed"
    assert hot_step["evidence"]["item"] == "Sales Model"


# ---------------------------------------------------------------------------
# run_diagnosis threads system_item_names through to diagnose_slowness
# ---------------------------------------------------------------------------

def test_run_diagnosis_passes_system_item_names_to_slowness():
    """run_diagnosis(symptom='slowness', ..., system_item_names=...) should thread the
    parameter through to diagnose_slowness."""
    events = [
        {"item": "Monitoring_ES", "user": "svc@internal", "cuSeconds": 100.0},
        {"item": "Sales", "user": "alice@co", "cuSeconds": 10.0},
        {"item": "HR", "user": "bob@co", "cuSeconds": 10.0},
        {"item": "Finance", "user": "carol@co", "cuSeconds": 10.0},
        {"item": "Ops", "user": "dave@co", "cuSeconds": 10.0},
    ]
    system_names = {"monitoring_es"}

    result = run_diagnosis("slowness", series=_SERIES_CALM, events=events,
                           system_item_names=system_names)
    hot_step = next(s for s in result["chain"]
                    if s["hypothesis"] == "One item dominates workload share")
    assert hot_step["verdict"] == "eliminated", (
        "run_diagnosis didn't thread system_item_names to diagnose_slowness"
    )
