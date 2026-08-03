"""System item kinds that must be excluded from concentration/user-ranking analyses.

Confirmed via four independent signals in 14 days of real tenant data (see
``GAPS-AND-ISSUES.md`` Section 12.6, and the ``SYSTEM_ITEM_KINDS`` entry in
``kb/metric_definitions.py``): three Fabric item kinds are driven by exactly one
system/service identity every hour of every day, with zero user-count variation and near-zero
CU-per-duration-sec -- a persistent-listener signature, not a workload. If concentration logic
ever runs against one of them, "1 user = 100% concentration" fires trivially and permanently.
That's a structurally-meaningless alert, not a real finding.

Kept as a small hardcoded set here because Fabric's item-kind vocabulary is canonical (defined
by Microsoft, not tenant-specific), and because a programmatic derivation from
user-count-variance / CU-per-duration heuristics would need per-item statistics the current
detector shape doesn't compute. Deriving the exclusion programmatically is a future
improvement noted in GAPS N5.

Case-insensitive match: Fabric's field values are consistent but exports/APIs occasionally
disagree on casing; a mismatch here would silently reintroduce the false positives this list
exists to prevent."""

SYSTEM_ITEM_KINDS = frozenset({
    "eventstream",
    "fabricevents-capacityutilizationevents",
    "activator",
})


def is_system_item_kind(kind):
    """True iff ``kind`` names a known system item kind (case-insensitive; None-safe).

    Returns False for missing/unknown kinds -- conservative: an item whose kind we can't read
    is NOT auto-excluded (better a false negative than falsely hiding a real workload)."""
    if kind is None:
        return False
    return str(kind).strip().lower() in SYSTEM_ITEM_KINDS
