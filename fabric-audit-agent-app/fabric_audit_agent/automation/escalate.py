"""Escalate a recurring Warning to Critical. Port of ``core/automation/escalate.js``.

Warning -> Critical when the same key was present in BOTH of the two most recent prior
runs (unresolved across 3 consecutive runs). Presence-based. Pure.
"""


def apply_escalation(findings, history):
    last_two = history[-2:]
    if len(last_two) < 2:
        return [{**f} for f in findings]

    def present_in_all(key):
        return all(any(rf.get("key") == key for rf in run["findings"]) for run in last_two)

    out = []
    for f in findings:
        if (f.get("score") or {}).get("level") == "Warning" and f.get("key") and present_in_all(f["key"]):
            out.append({**f, "score": {"level": "Critical", "reason": f"{f['score']['reason']} (escalated: unresolved 3 consecutive runs)"}})
        else:
            out.append({**f})
    return out
