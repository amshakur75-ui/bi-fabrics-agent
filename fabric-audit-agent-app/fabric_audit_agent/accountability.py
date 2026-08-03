"""Accountability: flag stale, repeatedly-ignored findings. Port of ``core/accountability.js``. Pure."""


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
        if runs >= threshold and open_:
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
