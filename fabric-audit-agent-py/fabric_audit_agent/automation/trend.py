"""Recurrence trend. Port of ``core/automation/trend.js``.

Annotate each finding with how many recent runs (window) contained its key, as
``recurringRuns`` (current run counts as 1). Pure.
"""


def annotate_recurring(findings, history, window=7):
    recent = history[-window:]
    out = []
    for f in findings:
        prior_hits = sum(1 for run in recent if any(rf.get("key") == f["key"] for rf in run["findings"])) if f.get("key") else 0
        out.append({**f, "recurringRuns": prior_hits + 1})
    return out
