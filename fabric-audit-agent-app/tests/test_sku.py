"""TDD tests for Task 6: honesty hardening — round_pct + sku_note."""
import pytest
from fabric_audit_agent.investigation.sku import round_pct, sku_note
from fabric_audit_agent.detectors.concentration import detect_concentration
from fabric_audit_agent.verdict import build_capacity_verdict


# ── round_pct ────────────────────────────────────────────────────────────────

def test_round_pct_clips_false_precision():
    assert round_pct(49.213063380823705) == 49.2


def test_round_pct_exact_integer_stays_integer_valued():
    assert round_pct(70.0) == 70.0


def test_round_pct_none_returns_none():
    assert round_pct(None) is None


def test_round_pct_zero():
    assert round_pct(0) == 0.0


def test_round_pct_rounds_at_half():
    assert round_pct(49.95) == 50.0


# ── sku_note ─────────────────────────────────────────────────────────────────

def test_sku_note_non_standard_returns_note():
    note = sku_note("FTL64")
    assert note is not None
    assert "trial" in note.lower() or "standard" in note.lower()
    assert "size-up" in note.lower() or "size up" in note.lower()


def test_sku_note_standard_f_sku_returns_none():
    for sku in ("F2", "F4", "F8", "F16", "F32", "F64", "F128", "F256", "F512", "F1024", "F2048"):
        assert sku_note(sku) is None, f"Expected None for standard SKU {sku!r}"


def test_sku_note_none_input_returns_none():
    assert sku_note(None) is None


def test_sku_note_empty_string_returns_note():
    # Empty string is not a standard SKU, so should return a note
    note = sku_note("")
    # empty is non-standard; a note is acceptable but we allow None for unknown/missing
    # The spec says "NOT a standard F2..F2048 name" -> returns note; empty string is not standard
    assert note is not None


def test_sku_note_trial_p_sku_returns_note():
    # P-SKUs (Premium) are not standard Fabric F-tier; should be flagged
    note = sku_note("P1")
    assert note is not None


# ── Integration: concentration finding has rounded share ─────────────────────

def test_concentration_finding_share_is_rounded():
    """The 'what' text and evidence sharePct must NOT show 15-decimal precision."""
    facts = {"items": [{
        "workspace": "Finance", "name": "GL Model", "kind": "SemanticModel",
        "cuSeconds": 700000, "sharePct": 49.213063380823705, "users": 12,
    }]}
    flags = detect_concentration(facts)
    assert len(flags) == 1
    # The evidence sharePct should be rounded to 1 decimal
    share_in_evidence = flags[0]["evidence"]["sharePct"]
    assert share_in_evidence == 49.2, f"Expected 49.2, got {share_in_evidence!r}"
    # The rendered 'what' text must not contain the raw 15-decimal value
    what = flags[0]["what"]
    assert "49.213063380823705" not in what
    assert "49.2" in what


# ── Integration: size-up verdict carries sku_note for non-standard SKU ───────

def _throttle_facts(sku):
    """Facts that produce a size-up verdict (throttling, no remaining optimizations)."""
    return {
        "capacity": {
            "tenant": "Contoso", "capacityId": "CAP1", "sku": sku,
            "peakCuPct": 96, "throttleMinutes": 42,
            "refreshes": [],   # no refreshes -> no contention/oversized -> size-up path
        }
    }


def test_size_up_verdict_carries_trial_note_for_nonstandard_sku():
    facts = _throttle_facts("FTL64")
    verdict = build_capacity_verdict(facts, [{"type": "capacity.throttle"}])
    assert verdict["decision"] == "size-up"
    # The verdict should carry a skuNote warning about trial / non-standard
    note = verdict["evidence"].get("skuNote")
    assert note is not None, "Expected skuNote in verdict evidence for non-standard SKU"
    assert "trial" in note.lower() or "standard" in note.lower()


def test_size_up_verdict_has_no_sku_note_for_standard_sku():
    facts = _throttle_facts("F64")
    verdict = build_capacity_verdict(facts, [{"type": "capacity.throttle"}])
    assert verdict["decision"] == "size-up"
    note = verdict["evidence"].get("skuNote")
    assert note is None, f"Expected no skuNote for standard SKU F64, got {note!r}"


def test_healthy_verdict_no_sku_note_required():
    facts = {"capacity": {
        "tenant": "C", "capacityId": "CAP1", "sku": "FTL64",
        "peakCuPct": 40, "throttleMinutes": 0, "refreshes": [],
    }}
    verdict = build_capacity_verdict(facts, [])
    assert verdict["decision"] == "healthy"
    # No sku_note requirement on healthy path (no size-up advice given)
