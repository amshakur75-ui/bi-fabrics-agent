"""Non-fatal facts validation. Port of ``core/validate.js``.

Missing domains are fine (just not audited); present-but-malformed shapes are reported.
"""
_REQUIRED_CAPACITY = ["capacityId", "sku", "memoryGB", "peakCuPct"]
_ARRAY_DOMAINS = ["models", "reports", "pipelines"]


def validate_facts(facts=None):
    facts = facts or {}
    issues = []
    cap = facts.get("capacity")
    if cap is not None:   # JS `if (facts.capacity)` — any object (incl. {}) enters
        for k in _REQUIRED_CAPACITY:
            if k not in cap:   # JS `=== undefined` (explicit null would not flag)
                issues.append({"domain": "capacity", "issue": f"missing {k}"})
        if "refreshes" in cap and not isinstance(cap["refreshes"], list):
            issues.append({"domain": "capacity", "issue": "refreshes must be an array"})
    for d in _ARRAY_DOMAINS:
        if d in facts and not isinstance(facts[d], list):
            issues.append({"domain": d, "issue": "expected an array"})
    if facts.get("lineage") and not isinstance(facts["lineage"].get("nodes"), list):
        issues.append({"domain": "lineage", "issue": "nodes must be an array"})
    return {"ok": len(issues) == 0, "issues": issues}


# ---------------------------------------------------------------------------
# B4 -- Math consistency check. Shape validation above catches malformed data; this catches
# a class this project has hit for real: a formula stated correctly but computed wrong, or two
# figures from different sources quietly reported side-by-side as if they reconciled when they
# don't (the real example that motivated this: 105.1% CU% reported alongside overageAddMs=786K
# CU-ms in the same response -- 105.1% implies ~1,567,000 CU-ms of overage at F1024, not 786K).
#
# NOTE: this function is intentionally NOT yet wired into any live call site (diagnose.py,
# throttle.py). Wiring it in touches control flow real data flows through and needs live test
# verification before it's trusted in production -- see tasks/todo.md Phase 3, N11. The pure
# function itself is safe to add now: it has no side effects and nothing currently calls it, so
# adding it changes no existing behavior.
# ---------------------------------------------------------------------------
class InconsistentSourcesError(ValueError):
    """Raised when two figures that should reconcile (a CU% and its implied overage-add, or any
    similar pair) disagree by more than the tolerance -- signals they were computed from
    different sources/windows and must not be reported side-by-side as if they agree."""


def assert_cu_consistency(cu_pct, overage_add_ms, base_cu, *, window_s=30, tolerance_pct=1.0):
    """Check that a window's CU% and its overage-add figure imply the same excess-over-100%.

    ``cu_pct`` is the window's total CU% (e.g. 105.1). ``overage_add_ms`` is the raw
    ``overageAddCapacityUnitMs`` for that SAME window. ``base_cu`` is the base capacity units
    (e.g. 1024 for F1024). Both figures describe the same window's overage, computed from two
    different fields -- they should imply the same excess-over-100% within a small tolerance.

    Only meaningful when ``cu_pct > 100`` (there is no overage to reconcile below threshold);
    returns ``True`` immediately in that case without checking anything.

    Raises ``InconsistentSourcesError`` (with both figures and what overage the CU% implies) when
    the discrepancy exceeds ``tolerance_pct`` percentage points -- the caller must then pick one
    source and recompute the other, per B4's finding, rather than present both unreconciled.

    Returns ``True`` when consistent. Never returns ``False`` -- either it's consistent, or it
    raises, so a caller can rely on "didn't raise" as "consistent" without an extra check.
    """
    if cu_pct is None or overage_add_ms is None or not base_cu:
        return True   # nothing to reconcile -- a missing figure is a different problem (gates.py)
    if cu_pct <= 100:
        return True   # no overage expected below threshold; nothing to check
    budget_ms = base_cu * 1000 * window_s
    implied_excess_pct = (overage_add_ms / budget_ms) * 100
    actual_excess_pct = cu_pct - 100
    if abs(implied_excess_pct - actual_excess_pct) > tolerance_pct:
        expected_overage_ms = actual_excess_pct / 100 * budget_ms
        raise InconsistentSourcesError(
            f"CU% of {cu_pct} implies ~{expected_overage_ms:.0f} CU-ms of overage-add for this "
            f"window, but overageAddCapacityUnitMs reported {overage_add_ms:.0f} -- a "
            f"{abs(implied_excess_pct - actual_excess_pct):.1f}pp discrepancy exceeds the "
            f"{tolerance_pct}pp tolerance. These two figures do not reconcile; do not report "
            f"both as if they agree -- recompute one from the other's source instead."
        )
    return True
