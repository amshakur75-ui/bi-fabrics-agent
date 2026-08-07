"""Task 8 (2026-07-29) regression:
- N5: system item kinds (EventStream / Activator / FabricEvents-CapacityUtilizationEvents)
  are excluded from concentration.py.
- N9: the 30% threshold has ONE source of truth
  (``config["capacity"]["concentrationPct"]``) -- gates.py + diagnose.py inline literals
  both derive from it, so a single edit propagates.

The N8 item-kind half of ``diagnose_slowness``'s hot-item computation is DEFERRED: neither the
per-user rollup (``rollup_attribution.users``) nor the raw event stream carries a Fabric item kind
today, so it can't tell an ``EventStream`` from a ``Dataset`` by construction. Fixing it requires
enriching the rollup + events with kind data first (separate ticket).

(N6, the item-kind filter on the per-user CU-blended concentration detector, is moot -- that
detector was retired; see tasks/tightening.md Part 1c/1d.)"""

from fabric_audit_agent.config import DEFAULT_CONFIG, merge_config
from fabric_audit_agent.detectors.concentration import detect_concentration
from fabric_audit_agent.detectors.system_item_kinds import (
    SYSTEM_ITEM_KINDS,
    is_system_item_kind,
)
from fabric_audit_agent.investigation import gates as _gates_mod
from fabric_audit_agent.investigation.diagnose import diagnose_slowness


# ---------------------------------------------------------------------------
# system_item_kinds helper -- direct unit tests
# ---------------------------------------------------------------------------

def test_system_item_kinds_covers_the_three_confirmed_kinds():
    assert "eventstream" in SYSTEM_ITEM_KINDS
    assert "activator" in SYSTEM_ITEM_KINDS
    assert "fabricevents-capacityutilizationevents" in SYSTEM_ITEM_KINDS


def test_is_system_item_kind_is_case_insensitive():
    assert is_system_item_kind("EventStream") is True
    assert is_system_item_kind("eventstream") is True
    assert is_system_item_kind("EVENTSTREAM") is True
    assert is_system_item_kind("Activator") is True
    assert is_system_item_kind("FabricEvents-CapacityUtilizationEvents") is True


def test_is_system_item_kind_none_safe_and_conservative():
    # None / unknown / blank: NOT auto-excluded (better a false negative than falsely hiding
    # a real workload).
    assert is_system_item_kind(None) is False
    assert is_system_item_kind("") is False
    assert is_system_item_kind("Dataset") is False
    assert is_system_item_kind("Report") is False
    assert is_system_item_kind("KustoEventHouse") is False


# ---------------------------------------------------------------------------
# N5: concentration.py must NOT flag system-item-kind items
# ---------------------------------------------------------------------------

def test_concentration_skips_eventstream_item_even_when_share_is_100pct():
    facts = {"items": [{
        "workspace": "SysOps", "name": "Monitoring_Eventstream", "kind": "EventStream",
        "sharePct": 100.0, "cuSeconds": 400000, "users": 1,
        "topUsers": [{"user": "system@internal"}], "userCount": 1,
    }]}
    flags = detect_concentration(facts)
    assert flags == [], (
        "N5 regression: an EventStream item at 100% share was flagged for concentration -- "
        "1 user / 100% is a structural signature of a system listener, not a workload"
    )


def test_concentration_skips_activator_and_fabricevents():
    facts = {"items": [
        {"workspace": "SysOps", "name": "some-activator", "kind": "Activator",
         "sharePct": 88.0, "cuSeconds": 1000, "users": 1,
         "topUsers": [{"user": "activator-sp@internal"}], "userCount": 1},
        {"workspace": "SysOps", "name": "cap-util", "kind": "FabricEvents-CapacityUtilizationEvents",
         "sharePct": 75.0, "cuSeconds": 500, "users": 1,
         "topUsers": [{"user": "fabric-svc@internal"}], "userCount": 1},
    ]}
    assert detect_concentration(facts) == []


def test_concentration_still_flags_a_real_dataset_alongside_a_system_item():
    """A real Dataset over threshold in the SAME facts as a system item still fires -- the
    exclusion is per-item, not a set-level short-circuit."""
    facts = {"items": [
        {"workspace": "SysOps", "name": "Monitoring_Eventstream", "kind": "EventStream",
         "sharePct": 100.0, "cuSeconds": 400000, "users": 1,
         "topUsers": [{"user": "system@internal"}], "userCount": 1},
        {"workspace": "Enterprise Sales", "name": "Ent-Reporting-Sales", "kind": "Dataset",
         "sharePct": 60.0, "cuSeconds": 146062000, "users": 278,
         "topUsers": [{"user": "heavy.user@company.com"}], "userCount": 278},
    ]}
    flags = detect_concentration(facts)
    assert len(flags) == 1
    assert flags[0]["resource"].endswith("Ent-Reporting-Sales")


def test_concentration_still_flags_when_kind_is_missing():
    """Conservative: if kind isn't present we DON'T auto-exclude. Sources like the LA rollup
    don't currently carry kind, so those items must continue to flow through the detector
    unchanged -- a rollup enrichment is a separate ticket."""
    facts = {"items": [{
        "workspace": "Finance", "name": "Sales Model",
        # NO "kind" field -- what the LA rollup produces today
        "sharePct": 50.0, "cuSeconds": 1000, "users": 3,
        "topUsers": [{"user": "alice@co"}], "userCount": 3,
    }]}
    flags = detect_concentration(facts)
    assert len(flags) == 1


# ---------------------------------------------------------------------------
# N9: threshold has ONE source of truth
# ---------------------------------------------------------------------------

def test_gates_concentration_threshold_matches_default_config():
    """gates.CONCENTRATION_THRESHOLD_PCT must be derived from DEFAULT_CONFIG, not a hardcoded
    duplicate. Change the config default and the gate constant follows."""
    assert _gates_mod.CONCENTRATION_THRESHOLD_PCT == float(
        DEFAULT_CONFIG["capacity"]["concentrationPct"]
    )


def test_diagnose_slowness_uses_config_threshold_not_hardcoded_30():
    """The old inline ``> 30.0`` literals in diagnose_slowness are gone -- the threshold now
    threads through from an optional ``config`` arg (default: DEFAULT_CONFIG). This test bumps
    the config threshold to 55 and asserts that a 40% hot item (above 30 but below 55) no
    longer flips the chain's verdict to CONFIRMED."""
    # Series: one over-100% window so the throttle branch fires and we reach the hot-item step.
    series = [{"epoch": 1_700_000_000, "cuPct": 137.4},
              {"epoch": 1_700_000_030, "cuPct": 120.0}]
    # Events: one item at ~40% share, one item at ~60% share -- baseline "> 30" would flag the
    # 60% item. Bumped to threshold 55, the 60% still flags. Bumped to 65, neither flags.
    events = [
        {"item": "A", "user": "alice", "cuSeconds": 60.0},
        {"item": "B", "user": "bob",   "cuSeconds": 40.0},
    ]

    # Default config (concentrationPct=30): A at 60% flips hot-item confirmed.
    default_out = diagnose_slowness(series, events, has_real_cost=True)
    hot_step_default = next(s for s in default_out["chain"]
                             if s["hypothesis"] == "One item dominates workload share")
    assert hot_step_default["verdict"] == "confirmed"
    assert "single hot item >30% share?" == hot_step_default["step"]

    # Custom config with threshold 65: A at 60% no longer confirms; the eliminated branch is taken.
    high_cfg = merge_config({"capacity": {"concentrationPct": 65}})
    high_out = diagnose_slowness(series, events, has_real_cost=True, config=high_cfg)
    hot_step_high = next(s for s in high_out["chain"]
                          if s["hypothesis"] == "One item dominates workload share")
    assert hot_step_high["verdict"] == "eliminated"
    # And the step name reflects the new threshold, so the chain remains self-describing.
    assert "single hot item >65% share?" == hot_step_high["step"]


def test_diagnose_slowness_user_hot_step_also_uses_config_threshold():
    """The hot-USER check (second inline literal) shares the same threshold source. Spread the
    load across many items so the hot-ITEM check doesn't short-circuit at CONFIRMED and we
    actually reach the hot-user step."""
    series = [{"epoch": 1_700_000_000, "cuPct": 137.4}]
    # 10 distinct items (~10% share each so no hot-item flip), but alice runs ~66% of the total.
    events = []
    for i in range(10):
        events.append({"item": f"item-{i}", "user": "alice", "cuSeconds": 20.0})
    for i in range(10):
        events.append({"item": f"item-{i}", "user": "bob", "cuSeconds": 10.0})
    # Baseline check: at default threshold (30) alice at ~66% is confirmed hot-user.
    default_out = diagnose_slowness(series, events, has_real_cost=True)
    default_user = next(s for s in default_out["chain"]
                         if s["hypothesis"] == "One user dominates workload share")
    assert default_user["verdict"] == "confirmed"

    # At threshold 70, alice at ~66% is eliminated.
    high_cfg = merge_config({"capacity": {"concentrationPct": 70}})
    high_out = diagnose_slowness(series, events, has_real_cost=True, config=high_cfg)
    high_user = next(s for s in high_out["chain"]
                      if s["hypothesis"] == "One user dominates workload share")
    assert high_user["verdict"] == "eliminated"
