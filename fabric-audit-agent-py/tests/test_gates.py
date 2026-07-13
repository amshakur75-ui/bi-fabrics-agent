"""Tests for the investigation STOP-gates (investigation/gates.py). Pure/offline."""
from fabric_audit_agent.investigation.gates import (
    throttle_claim_gate,
    pressure_claim_gate,
    concentration_gate,
    null_data_gate,
    verdict_gate,
    true_cu_per_user_gate,
)


# ---- throttle vs pressure: two claims, two gates ----
def test_throttle_gate_passes_only_on_throttle_signal():
    out = throttle_claim_gate({"peakCuPct": 147.8, "throttleMinutes": 17})
    assert out["passed"] is True
    assert out["signal"]["throttleMinutes"] == 17


def test_throttle_gate_blocks_on_high_cu_alone():
    # CU% > 100 with NO throttle signal — smoothing absorbs bursts; the claim is BLOCKED.
    out = throttle_claim_gate({"peakCuPct": 130.0, "throttleMinutes": 0})
    assert out["passed"] is False
    assert "not throttling" in out["note"].lower() or "no throttle" in out["note"].lower()


def test_throttle_gate_blocks_on_missing_data():
    assert throttle_claim_gate({})["passed"] is False
    assert throttle_claim_gate(None)["passed"] is False


def test_pressure_gate_is_the_separate_cu_claim():
    assert pressure_claim_gate({"peakCuPct": 130.0})["passed"] is True
    assert pressure_claim_gate({"peakCuPct": 88.0})["passed"] is False
    assert pressure_claim_gate(None)["passed"] is False


def test_throttle_and_pressure_can_disagree():
    # pressure yes / throttle no — the classic smoothing case the agent must distinguish.
    cap = {"peakCuPct": 115.0, "throttleMinutes": 0}
    assert pressure_claim_gate(cap)["passed"] is True
    assert throttle_claim_gate(cap)["passed"] is False


# ---- concentration: pass + mandatory proxy label ----
def test_concentration_gate_passes_over_threshold_with_proxy_label():
    out = concentration_gate(44.0)
    assert out["passed"] is True
    assert "proxy" in out["label"].lower()          # CPU-proxy label is mandatory
    assert "billed" not in out["label"].lower() or "not billed" in out["label"].lower()


def test_concentration_gate_blocks_under_threshold():
    assert concentration_gate(22.0)["passed"] is False


def test_concentration_gate_custom_threshold_and_none():
    assert concentration_gate(25.0, threshold=20)["passed"] is True
    assert concentration_gate(None)["passed"] is False


# ---- null data: INCONCLUSIVE, never healthy ----
def test_null_data_gate_empty_is_inconclusive():
    for empty in (None, [], {}, {"error": "timeout"}):
        out = null_data_gate(empty)
        assert out["conclusive"] is False
        assert out["verdict"] == "inconclusive"
        assert "unavailable" in out["reason"].lower() or "no data" in out["reason"].lower()


def test_null_data_gate_rows_are_conclusive():
    out = null_data_gate([{"k": 1}])
    assert out["conclusive"] is True


def test_null_data_gate_error_payload_is_inconclusive_even_with_other_keys():
    out = null_data_gate({"error": "401", "rows": []})
    assert out["conclusive"] is False


# ---- verdict gates: size-up needs persistence + distribution ----
def _run(throttled, top_share=10.0):
    return {"throttleMinutes": 17 if throttled else 0, "topItemSharePct": top_share}


def test_sizeup_gate_requires_persistent_throttle_and_distributed_load():
    out = verdict_gate(
        current={"peakCuPct": 140, "throttleMinutes": 20},
        history_signals=[_run(True), _run(True)],
        top_item_share_pct=12.0,
    )
    assert out["sizeUpEligible"] is True


def test_sizeup_gate_blocked_by_single_dominant_item():
    out = verdict_gate(
        current={"peakCuPct": 140, "throttleMinutes": 20},
        history_signals=[_run(True), _run(True)],
        top_item_share_pct=61.0,   # one item dominates -> optimize first
    )
    assert out["sizeUpEligible"] is False
    assert out["optimizeEligible"] is True


def test_sizeup_gate_blocked_without_persistence():
    out = verdict_gate(
        current={"peakCuPct": 140, "throttleMinutes": 20},
        history_signals=[_run(False), _run(False)],   # first time throttling
        top_item_share_pct=12.0,
    )
    assert out["sizeUpEligible"] is False


def test_verdict_gate_no_throttle_now_neither_eligible():
    out = verdict_gate(
        current={"peakCuPct": 80, "throttleMinutes": 0},
        history_signals=[_run(True)],
        top_item_share_pct=50.0,
    )
    assert out["sizeUpEligible"] is False and out["optimizeEligible"] is False


def test_verdict_gate_none_safe():
    out = verdict_gate(current=None, history_signals=None, top_item_share_pct=None)
    assert out["sizeUpEligible"] is False


# ---- true CU per user: permanently blocked ----
def test_true_cu_gate_always_blocked_with_direction():
    out = true_cu_per_user_gate()
    assert out["passed"] is False
    assert out["blocked"] is True
    assert "metrics app" in out["note"].lower()      # directs the admin to the right place
    assert "timepoint" in out["note"].lower()
