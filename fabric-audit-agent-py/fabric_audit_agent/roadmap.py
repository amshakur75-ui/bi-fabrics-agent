"""Remediation roadmap. Faithful port of the Node ``core/roadmap.js``.

Order findings: severity first, then longest-standing (recurringRuns) first. Pure.
"""
_SEV_RANK = {"Critical": 0, "Warning": 1, "Info": 2}


def _recurring(f):
    r = f.get("recurringRuns")
    return r if r is not None else 1   # nullish default, matches JS ?? 1


def build_roadmap(findings=None):
    findings = findings or []
    ranked = sorted(
        findings,
        key=lambda f: (_SEV_RANK.get((f.get("score") or {}).get("level"), 9), -_recurring(f)),
    )
    out = []
    for i, f in enumerate(ranked):
        lvl = (f.get("score") or {}).get("level")
        fix = f.get("fix")
        out.append({
            "rank": i + 1,
            "key": f.get("key"),
            "level": lvl if lvl is not None else "Info",
            "what": f.get("what"),
            "fix": fix[0] if isinstance(fix, list) and fix else None,
            "recurringRuns": _recurring(f),
        })
    return out
