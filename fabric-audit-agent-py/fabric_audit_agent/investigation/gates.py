"""Investigation STOP-gates — deterministic checks a claim must pass before the agent may make it.

The anti-hallucination layer of the investigation harness (Phase: investigation-harness, spec
2026-07-13). Each gate is a PURE function returning a small dict the harness/tools cite verbatim in
answers; the LLM cannot talk its way past a gate that did not fire because the gate result (with the
signal value that fired it) is what gets quoted, not the model's paraphrase.

Grounding (verified vs Microsoft docs, deep-research run wf_2bd7708f-99f, all claims 3-0):
- Throttling and CU-pressure are DIFFERENT claims with different gates: smoothing absorbs bursts, so
  CU% > 100 alone is NOT throttling ("Since the capacity never goes over 100% ... capacity throttling
  isn't the cause. End of analysis." — learn.microsoft.com/fabric/enterprise/capacity-planning-
  troubleshoot-throttling). A throttle claim needs an actual throttle signal in-window.
- Size-up vs optimize: "Consistently high throttling levels indicate the need to ... increase the
  capacity's SKU size" (plan-capacity) — persistent + distributed; a single dominant item means
  optimize first (sizing up would mask a fixable problem).
- True billed CU per user exists only in the Capacity Metrics app's Timepoint Item Detail page, which
  is blocked to service principals — permanently a human step; per-user CPU figures are a proxy.
"""

CONCENTRATION_THRESHOLD_PCT = 30.0
# One item over this share of monitored CU means "optimize that item first", not "buy a bigger SKU".
DOMINANT_ITEM_SHARE_PCT = 40.0

_PROXY_LABEL = ("share of monitored CU (a CPU-time proxy from telemetry, not billed capacity CU)")

_TRUE_CU_NOTE = (
    "True billed CU per user is only available in the Fabric Capacity Metrics app - Compute page -> "
    "click the timepoint -> Timepoint Item Detail page (User column). That surface blocks service "
    "principals, so this agent can never read it; per-user figures here are a CPU-time proxy."
)


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def throttle_claim_gate(capacity):
    """Gate for the claim "throttling occurred". Passes ONLY on an actual throttle signal
    (``throttleMinutes`` > 0 in the window) — never on high CU alone (smoothing absorbs bursts)."""
    cap = capacity or {}
    minutes = _num(cap.get("throttleMinutes"))
    if minutes is not None and minutes > 0:
        return {"passed": True, "claim": "throttling occurred",
                "signal": {"throttleMinutes": minutes}}
    peak = _num(cap.get("peakCuPct"))
    if peak is not None and peak > 100:
        note = (f"CU peaked at {peak}% but no throttle signal fired in the window - high utilization "
                "alone is not throttling (smoothing absorbs bursts). The throttling claim is blocked; "
                "the separate CU-pressure claim may still stand.")
    else:
        note = "No throttle signal in the data - the throttling claim is blocked."
    return {"passed": False, "claim": "throttling occurred", "signal": None, "note": note}


def pressure_claim_gate(capacity):
    """Gate for the SEPARATE claim "the capacity exceeded its CU ceiling" (peakCuPct > 100)."""
    peak = _num((capacity or {}).get("peakCuPct"))
    if peak is not None and peak > 100:
        return {"passed": True, "claim": "CU exceeded 100%", "signal": {"peakCuPct": peak}}
    return {"passed": False, "claim": "CU exceeded 100%", "signal": None,
            "note": "peakCuPct did not exceed 100% in the window (or was unavailable)."}


def concentration_gate(share_pct, threshold=CONCENTRATION_THRESHOLD_PCT):
    """Gate for the claim "one user/item holds a disproportionate share". The result carries the
    mandatory CPU-proxy label - a passing concentration figure must NEVER be presented as billed CU."""
    share = _num(share_pct)
    if share is not None and share > threshold:
        return {"passed": True, "claim": f"concentration over {threshold}%",
                "signal": {"sharePct": share}, "label": _PROXY_LABEL}
    return {"passed": False, "claim": f"concentration over {threshold}%", "signal": None,
            "label": _PROXY_LABEL}


def null_data_gate(payload):
    """Absence of data is NOT absence of problems. An empty/failed source makes the verdict
    INCONCLUSIVE ("data unavailable"), never HEALTHY ("no problems found")."""
    inconclusive = {"conclusive": False, "verdict": "inconclusive",
                    "reason": ("Source returned no data or an error - data unavailable. This cannot "
                               "be read as healthy; it means the check could not run.")}
    if payload is None:
        return inconclusive
    if isinstance(payload, dict):
        if payload.get("error") is not None or not payload:
            return inconclusive
        return {"conclusive": True}
    if isinstance(payload, (list, tuple)):
        return {"conclusive": True} if len(payload) > 0 else inconclusive
    return {"conclusive": True}


def verdict_gate(current, history_signals, top_item_share_pct):
    """Composable size-up/optimize eligibility on top of ``build_capacity_verdict``.

    SIZE-UP eligible iff: throttling NOW (throttle gate passes on *current*) AND PERSISTENT (a throttle
    signal in at least one prior run) AND DISTRIBUTED (no single item over DOMINANT_ITEM_SHARE_PCT).
    OPTIMIZE eligible iff: throttling now AND a single dominant item exists (named fixable target).
    Neither eligible when not throttling now - and an empty history can never establish persistence.
    """
    now = throttle_claim_gate(current)
    if not now["passed"]:
        return {"sizeUpEligible": False, "optimizeEligible": False,
                "reason": "not throttling in the current window"}
    prior = [r for r in (history_signals or [])
             if _num((r or {}).get("throttleMinutes")) and (r or {}).get("throttleMinutes") > 0]
    persistent = len(prior) >= 1
    share = _num(top_item_share_pct)
    dominant = share is not None and share > DOMINANT_ITEM_SHARE_PCT
    return {
        "sizeUpEligible": persistent and not dominant,
        "optimizeEligible": dominant,
        "persistentThrottle": persistent,
        "dominantItemSharePct": share if dominant else None,
        "reason": ("persistent throttling with distributed load" if (persistent and not dominant) else
                   "a single item dominates - optimize it before sizing up" if dominant else
                   "first observed throttle - not yet persistent enough to justify a size-up"),
    }


def true_cu_per_user_gate():
    """PERMANENTLY BLOCKED: the agent may direct an admin to the Metrics app, never state the figure."""
    return {"passed": False, "blocked": True, "claim": "true billed CU per user",
            "note": _TRUE_CU_NOTE}
