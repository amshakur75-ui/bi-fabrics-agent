"""Task 9 (E1) regression: concentration.py must warn when the items feeding it carry a
MIX of attribution modes -- ``sharePct`` was formed with a denominator that mixed cost signals,
so the ratio silently blends two different metrics. We can't recompute the ratio (that would
require re-running the rollup), so we tag every emitted flag with a caveat + ``mixedSources``
evidence field a caller/agent can act on."""
from fabric_audit_agent.detectors.concentration import detect_concentration


_ALICE_TOPUSERS = [{"user": "alice@co", "cuSeconds": 100.0}]
_BOB_TOPUSERS = [{"user": "bob@co", "cuSeconds": 100.0}]


def test_pure_cost_cpu_items_no_mixed_flag():
    facts = {"items": [
        {"workspace": "Finance", "name": "Sales", "kind": "Dataset",
         "sharePct": 60.0, "cuSeconds": 60, "attributionMode": "cost-cpu",
         "topUsers": _ALICE_TOPUSERS, "userCount": 1},
        {"workspace": "Ops", "name": "Logistics", "kind": "Dataset",
         "sharePct": 40.0, "cuSeconds": 40, "attributionMode": "cost-cpu",
         "topUsers": _BOB_TOPUSERS, "userCount": 1},
    ]}
    flags = detect_concentration(facts)
    assert len(flags) == 2
    for f in flags:
        assert f["evidence"].get("mixedSources") is None, (
            "no mixed-source warning should fire when all items share the same attribution mode"
        )
        assert "mixes cost bases" not in f["what"]


def test_pure_cost_duration_items_no_mixed_flag():
    """Homogeneous cost-duration is also fine -- one mode across all items."""
    facts = {"items": [
        {"workspace": "Finance", "name": "Sales", "kind": "Dataset",
         "sharePct": 55.0, "cuSeconds": 55, "attributionMode": "cost-duration",
         "topUsers": _ALICE_TOPUSERS, "userCount": 1},
        {"workspace": "Ops", "name": "Logistics", "kind": "Dataset",
         "sharePct": 45.0, "cuSeconds": 45, "attributionMode": "cost-duration",
         "topUsers": _BOB_TOPUSERS, "userCount": 1},
    ]}
    flags = detect_concentration(facts)
    assert len(flags) == 2
    assert all(f["evidence"].get("mixedSources") is None for f in flags)


def test_mixed_cost_cpu_and_cost_duration_tags_every_flag():
    """The E1 scenario: sharePct was computed with a denominator that summed BOTH cost-cpu
    and cost-duration rows. Every emitted concentration flag must carry the mixed-sources
    caveat so the caller knows the percentage silently blends two metrics."""
    facts = {"items": [
        {"workspace": "Finance", "name": "Sales", "kind": "Dataset",
         "sharePct": 60.0, "cuSeconds": 60, "attributionMode": "cost-cpu",
         "topUsers": _ALICE_TOPUSERS, "userCount": 1},
        {"workspace": "Ops", "name": "Logistics", "kind": "Dataset",
         "sharePct": 40.0, "cuSeconds": 40, "attributionMode": "cost-duration",
         "topUsers": _BOB_TOPUSERS, "userCount": 1},
    ]}
    flags = detect_concentration(facts)
    assert len(flags) == 2
    for f in flags:
        assert f["evidence"].get("mixedSources") is True, (
            "E1 regression: a mixed-mode input must set mixedSources=True on every emitted flag"
        )
        assert "cost-cpu" in f["evidence"]["mixedSourcesNote"]
        assert "cost-duration" in f["evidence"]["mixedSourcesNote"]
        assert "mixes cost bases" in f["what"], (
            "the plain-language 'what' string must carry the caveat inline, not only in evidence"
        )


def test_system_item_kinds_do_not_count_as_a_second_mode():
    """A system item (excluded from concentration per N5) must NOT be counted as a second
    attribution mode -- if the only cost-cpu carrier is an EventStream that's been excluded,
    the remaining Dataset items are still homogeneous cost-duration."""
    facts = {"items": [
        {"workspace": "SysOps", "name": "Monitoring_Eventstream", "kind": "EventStream",
         "sharePct": 100.0, "cuSeconds": 500, "attributionMode": "cost-cpu",
         "topUsers": [{"user": "system@internal"}], "userCount": 1},
        {"workspace": "Ent", "name": "Sales", "kind": "Dataset",
         "sharePct": 55.0, "cuSeconds": 55, "attributionMode": "cost-duration",
         "topUsers": _ALICE_TOPUSERS, "userCount": 1},
    ]}
    flags = detect_concentration(facts)
    # EventStream is excluded (N5) -> exactly one Dataset flag -> mode set is {cost-duration}
    # -> not mixed.
    assert len(flags) == 1
    assert flags[0]["evidence"].get("mixedSources") is None


def test_missing_attribution_mode_does_not_count_as_a_second_mode():
    """Items with attributionMode=None (typical CSV/REST source that pre-dates the split) MUST
    NOT be counted as a second mode -- treating None as its own mode would flip every audit run
    that mixes a CSV source with an LA source into 'mixed', which is noise. The upstream
    rollup already labels None appropriately (or as a hedged 'monitored CU' by N3)."""
    facts = {"items": [
        {"workspace": "Finance", "name": "Sales", "kind": "Dataset",
         "sharePct": 60.0, "cuSeconds": 60, "attributionMode": None,
         "topUsers": _ALICE_TOPUSERS, "userCount": 1},
        {"workspace": "Ops", "name": "Logistics", "kind": "Dataset",
         "sharePct": 40.0, "cuSeconds": 40, "attributionMode": "cost-cpu",
         "topUsers": _BOB_TOPUSERS, "userCount": 1},
    ]}
    flags = detect_concentration(facts)
    assert all(f["evidence"].get("mixedSources") is None for f in flags)
