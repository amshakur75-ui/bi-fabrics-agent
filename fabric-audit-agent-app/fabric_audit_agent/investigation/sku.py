"""SKU helpers for honesty hardening (Phase 3, Task 6).

``round_pct``  — clip false precision to 1 decimal (kills ``49.213063380823705``).
``sku_note``   — flag non-standard / trial SKU names so "size-up" advice is not
                 blindly emitted for capacities that aren't real F-tier instances
                 (e.g. ``FTL64`` is a trial capacity where the answer is *not*
                 "buy a bigger SKU").

Pure stdlib; None-guard convention (not falsy ``or``).
"""
import re as _re

# Standard Fabric F-tier SKU names.  Only these earn unconditional "size-up" advice.
_STANDARD_F_SKUS = frozenset(
    f"F{n}" for n in (2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048)
)

_NOTE = (
    "Non-standard or trial SKU — verify capacity type before acting on size-up advice "
    "(trial capacities have different upgrade paths; size-up may not apply)."
)


def round_pct(x) -> float:
    """Round a percentage to 1 decimal place.  Returns None if *x* is None."""
    if x is None:
        return None
    return round(float(x), 1)


def sku_note(sku) -> str | None:
    """Return a warning note when *sku* is NOT a standard ``F2``–``F2048`` name.

    Returns ``None`` for standard SKUs (no note needed).
    Returns a non-empty string for anything else (trial, P-tier, empty, unknown).
    """
    if sku is None:
        return None
    if sku in _STANDARD_F_SKUS:
        return None
    return _NOTE


def _pos_int(x):
    """Coerce *x* to a positive int, else None (rejects bool, NaN/Inf, <=0, junk)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = int(float(x))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def check_sku_base_consistency(configured_base, live_base) -> dict | None:
    """Cross-check the SKU-/config-derived base CU against the LIVE base the capacity API
    reports (plan item 4.11 — the highest-risk open item).

    ``configured_base``: the base capacity units implied by the reported SKU name (via
    ``timepoint_peaks.base_cu_from_sku``) or the ``FABRIC_BASE_CU`` static default.
    ``live_base``: ``baseCapacityUnits`` read fresh from the capacity-events stream — the
    authoritative value the agent actually computes every ``%``-of-base figure against.

    Returns ``None`` when the check CANNOT run (either side unknown / non-positive) — a
    silent no-op, so a caller with no live source is unaffected. Otherwise returns a dict:
    ``{"skuMismatch": bool, "configuredBaseCu": int, "liveBaseCu": int}`` and, on a mismatch,
    a loud ``"note"`` explaining that every percentage was computed against the LIVE value and
    the reported SKU is stale / the capacity was resized. Pure/stdlib; None-guard convention
    (a real ``0`` is rejected as a base, so falsy-vs-None is not a concern here).
    """
    cfg = _pos_int(configured_base)
    live = _pos_int(live_base)
    if cfg is None or live is None:
        return None
    if cfg == live:
        return {"skuMismatch": False, "configuredBaseCu": cfg, "liveBaseCu": live}
    return {
        "skuMismatch": True,
        "configuredBaseCu": cfg,
        "liveBaseCu": live,
        "note": (
            f"SKU/base-CU MISMATCH: the reported SKU implies base {cfg} CU, but the live "
            f"capacity API reports base {live} CU. Every %-of-base figure here is computed "
            f"against the LIVE value ({live}); the reported SKU name is stale or the capacity "
            f"was resized. Verify the SKU before acting on any size-up advice or %-of-base claim."
        ),
    }
