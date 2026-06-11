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
