"""Estate health score. Faithful port of the Node ``core/health-score.js``.

100 = clean; each finding subtracts by severity weight, floored at 0. Overall + per-domain.
"""
from .key_utils import domain_of

_WEIGHT = {"Critical": 8, "Warning": 3, "Info": 1}


def _penalty(findings):
    return sum(_WEIGHT.get((f.get("score") or {}).get("level"), 0) for f in findings)


def build_health_score(findings=None):
    findings = findings or []
    overall = max(0, 100 - _penalty(findings))
    groups = {}
    for f in findings:
        groups.setdefault(domain_of(f.get("key")), []).append(f)
    by_domain = {d: max(0, 100 - _penalty(fs)) for d, fs in groups.items()}
    return {"overall": overall, "byDomain": by_domain}
