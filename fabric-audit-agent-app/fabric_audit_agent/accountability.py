"""Accountability: flag stale, repeatedly-ignored findings. Port of ``core/accountability.js``. Pure."""

# FIX 3: physical capacity states that come and go and AUTO-RESOLVE on their own (the capacity
# stops throttling/pressuring/overaging once load drops) — they must never get "you failed to
# resolve this" language, unlike a genuine standing problem (a failing refresh, a bad query shape).
# Exact strings match what detectors/capacity.py (capacity.throttle) and the pressure/overage
# check-types (automation/tier2_check.py, automation/sweep_delivery.py) emit. Shared with sla.py
# (which imports this module already for first_seen_map) rather than duplicated.
AUTO_RESOLVING_TYPES = frozenset({"capacity.throttle", "capacity.pressure", "capacity.overage"})


def type_of(key):
    """Finding-``type`` prefix of a ``"type::resource"`` key (see ``reasoner_stub.py``)."""
    return key.split("::")[0] if isinstance(key, str) else None


def first_seen_map(history=None):
    """Earliest run timestamp per key from chronological history (oldest first)."""
    history = history or []
    seen = {}
    for run in history:
        for rf in (run.get("findings") or []):
            if rf.get("key") and rf["key"] not in seen:
                seen[rf["key"]] = run.get("runAt")
    return seen


def annotate_accountability(findings, history=None, threshold=3):
    history = history or []
    first_seen = first_seen_map(history)
    out = []
    for f in findings:
        runs = f.get("recurringRuns") if f.get("recurringRuns") is not None else 1
        state = (f.get("lifecycle") or {}).get("state")
        open_ = (state if state is not None else "open") == "open"   # nullish (?? 'open'), not falsy
        auto_resolving = type_of(f.get("key")) in AUTO_RESOLVING_TYPES
        if runs >= threshold and open_ and not auto_resolving:
            out.append({**f, "accountability": {
                "openRuns": runs,
                "firstSeen": first_seen.get(f.get("key")),
                "message": f"Open for {runs} consecutive run(s) with no resolution.",
            }})
        else:
            out.append(f)
    return out


def summarize_accountability(findings=None):
    findings = findings or []
    ignored = [f for f in findings if f.get("accountability")]
    return {
        "ignoredCount": len(ignored),
        "items": [{"key": f.get("key"), "openRuns": f["accountability"]["openRuns"], "firstSeen": f["accountability"]["firstSeen"]} for f in ignored],
    }
