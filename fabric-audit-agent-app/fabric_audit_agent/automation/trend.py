"""Recurrence trend. Port of ``core/automation/trend.js``.

Annotate each finding with how many recent runs (window) contained its key, as
``recurringRuns`` (current run counts as 1). Pure.
"""


def annotate_recurring(findings, history, window=24):
    recent = history[-window:]
    out = []
    for f in findings:
        key = f.get("key")
        if not key:
            out.append({**f, "recurringRuns": 1, "firstSeenAt": None})
            continue
        matching_runs = [run for run in recent
                         if any(rf.get("key") == key for rf in run.get("findings", []))]
        first_seen_at = matching_runs[0].get("runAt") if matching_runs else None
        out.append({**f, "recurringRuns": len(matching_runs) + 1, "firstSeenAt": first_seen_at})
    return out
