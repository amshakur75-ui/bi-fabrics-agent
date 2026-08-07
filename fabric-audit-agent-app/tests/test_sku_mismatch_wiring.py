"""Plan 4.11 wiring: ``tools._sku_mismatch_flag`` fires ONLY on the live base-CU path.

The pure comparison lives in ``investigation.sku.check_sku_base_consistency`` (tested in
test_sku.py). This exercises the tools.py glue that decides WHEN it is safe to cross-check —
only when the base the agent used came from the LIVE capacity-events stream, so the SKU-implied
base is an independent second number to compare. The true live-fire assertion (a real resized
capacity) is deferred to the 7.2 live checklist; here we drive the branch deterministically.
"""
from fabric_audit_agent.tools import _sku_mismatch_flag


def test_live_path_mismatch_fires():
    # base used = live 1024, but SKU name "F64" implies 64 -> loud mismatch.
    flag = _sku_mismatch_flag(1024, "live-capacity-events", "F64")
    assert flag is not None
    assert flag["skuMismatch"] is True
    assert flag["liveBaseCu"] == 1024 and flag["configuredBaseCu"] == 64


def test_live_path_agreement_is_silent():
    # base used = live 64 and SKU "F64" implies 64 -> no flag (clean output unchanged).
    assert _sku_mismatch_flag(64, "live-capacity-events", "F64") is None


def test_nonlive_source_never_flags():
    # When the base came from the SKU/env (no live source), there is no independent second
    # number to compare, so we must NOT invent a mismatch — return None regardless.
    assert _sku_mismatch_flag(64, "sku-name", "F64") is None
    assert _sku_mismatch_flag(512, "env-default", "F64") is None
    assert _sku_mismatch_flag(999, "explicit-arg", "F64") is None


def test_unknown_sku_on_live_path_is_silent():
    # A trial/non-standard SKU implies no base -> nothing to compare -> None.
    assert _sku_mismatch_flag(1024, "live-capacity-events", "FTL64") is None
    assert _sku_mismatch_flag(1024, "live-capacity-events", None) is None
