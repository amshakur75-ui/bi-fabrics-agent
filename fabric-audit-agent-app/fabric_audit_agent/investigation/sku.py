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
