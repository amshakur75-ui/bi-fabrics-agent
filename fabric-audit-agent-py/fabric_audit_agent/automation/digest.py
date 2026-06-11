"""Digest rollup for a run. Port of ``core/automation/digest.js``. Pure."""
from ..key_utils import domain_of


def build_digest(findings, history):
    totals = {"Critical": 0, "Warning": 0, "Info": 0}
    by_domain = {}
    for f in findings:
        lvl = (f.get("score") or {}).get("level")
        lvl = lvl if lvl is not None else "Info"
        totals[lvl] = totals.get(lvl, 0) + 1
        d = domain_of(f.get("key"))
        by_domain[d] = by_domain.get(d, 0) + 1
    prev = history[-1]["findings"] if history else []
    prev_keys = {r.get("key") for r in prev}
    new_count = sum(1 for f in findings if f.get("key") and f["key"] not in prev_keys)
    recurring = [
        {"key": f.get("key"), "recurringRuns": f.get("recurringRuns"), "level": (f.get("score") or {}).get("level")}
        for f in findings
        if (f.get("recurringRuns") if f.get("recurringRuns") is not None else 1) >= 3
    ]
    return {"totals": totals, "byDomain": by_domain, "newCount": new_count, "recurring": recurring}
