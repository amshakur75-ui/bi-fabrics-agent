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
    "activity": {
        "slowOperationSeconds": 300, "highCuSeconds": 100,
        "recurringShapeMinCount": 3, "recurringShapeMinUsers": 2,
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
