"""Detection thresholds. Faithful port of the Node ``core/config.js``.

Config keys are kept camelCase to mirror the Node version 1:1 (minimises port risk and
matches how the detectors read them).
"""

DEFAULT_CONFIG = {
    "capacity": {
        "throttleWarnPct": 80, "throttleCritPct": 90, "throttleCritMinutes": 30,
        "contentionMin": 3, "contentionCritCount": 4, "oversizedGB": 4, "oversizedCritPct": 25,
        "concentrationPct": 30, "concentrationCritPct": 50,
    },
    "model": {"bidirectionalMin": 4, "bidirectionalCritMin": 8, "refreshFailPct": 10, "refreshFailCritPct": 25},
    "report": {"visualsMin": 20, "visualsCritMin": 40, "slowVisualMs": 5000, "slowVisualCritMs": 10000},
    "pipeline": {"failRatePct": 10},
    "security": {"unusualRatio": 5, "unusualCritRatio": 10},
    "cost": {"idleCuPct": 5},
    "refresh": {"retryStormAttempts": 3, "slowDataPhaseMin": 60, "chronicFailureCount": 3},
    "crossWorkspace": {"minWorkspaces": 3},   # B4: an anti-pattern in >= N workspaces is systemic
    # tightening.md Part 1a: absolute-cost thresholds for a single operation — pure Log Analytics
    # fact, independent of any share-of-capacity. Part 1b: recurring-shape thresholds — a query
    # SHAPE recurring across >= minCount events from >= minUsers distinct users points at a
    # model/report design problem, not a person problem.
    # Part 12 Category 4: long-running-cluster thresholds -- the SAME item accumulating
    # >= minCount independently-long (>= longRunningSeconds) operations points at the item's
    # design, not any one user. Distinct from the single-operation "activity.slow-operation".
    "activity": {
        "slowOperationSeconds": 300, "highCuSeconds": 100,
        "recurringShapeMinCount": 3, "recurringShapeMinUsers": 2,
        "longRunningSeconds": 300, "longRunningClusterMin": 3,
        # baselineMinHistory: minimum per-user samples before a baseline is trusted enough to
        # flag against. Deliberately <= the floor the nightly bootstrap writes with
        # (FABRIC_BASELINE_MIN_HISTORY, default 20), i.e. permissive. The dangerous direction is
        # reader > writer: rows the writer DID emit would fall into the gap and be silently
        # demoted to the estate baseline instead of used, which is exactly the misattribution
        # the 3-layer fallback exists to prevent. Keep this at or below the writer's floor.
        "baselineMinHistory": 5,
        # baselineSpikeMultiplier: an event must exceed the baseline p95 by THIS FACTOR to count
        # as an anomaly. A bare `cu > p95` comparison is a percentile lookup, not an anomaly
        # test — it fires on ~5% of all events by construction, forever, on a healthy capacity.
        # 3x on a long-tailed cost distribution puts it well under 0.1%.
        "baselineSpikeMultiplier": 3.0,
        # baselineSpikeFloorCuSeconds: absolute floor, so a user with a tiny baseline can't trip
        # on noise (p95=0.10 CPU-s -> 0.31 CPU-s is "3x" but meaningless). Reuses the same scale
        # as highCuSeconds.
        "baselineSpikeFloorCuSeconds": 100,
        # baselineMaxAgeDays: refuse a baseline older than this and fall through to the next
        # fallback layer. The nightly job fails QUIETLY (returns rowsWritten=0 rather than
        # raising), so without this a three-week-old p95 would keep being presented as
        # "their own baseline".
        "baselineMaxAgeDays": 3,
    },
}


def merge_config(overrides=None):
    """Deep-merge per-domain overrides onto the defaults (one level deep). Pure.

    Returns a new config; ``DEFAULT_CONFIG`` is never mutated. Unknown domains the
    caller adds are carried through.
    """
    overrides = overrides or {}
    out = {domain: {**defaults, **(overrides.get(domain) or {})} for domain, defaults in DEFAULT_CONFIG.items()}
    for key, val in overrides.items():
        if key not in out:
            out[key] = val
    return out
