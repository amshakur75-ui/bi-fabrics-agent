"""Estate health score. Faithful port of the Node ``core/health-score.js``.

100 = clean; each finding subtracts by severity weight, floored at 0. Overall + per-domain.
"""
from .key_utils import domain_of

_WEIGHT = {"Critical": 8, "Warning": 3, "Info": 1}


def _penalty(findings):
    return sum(_WEIGHT.get((f.get("score") or {}).get("level"), 0) for f in findings)


def build_health_score(findings=None, data_quality=None):
    """Overall + per-domain score. ``data_quality`` is ``validate_facts`` output: when the
    collection itself was degraded, the score is NOT a clean bill of health.

    Zero findings is indistinguishable from a clean estate, so a blind collection scored 100/100 and
    the narrative said "Estate health is 100/100 with 0 critical and 0 warning finding(s)" -- handed
    verbatim to the chat agent -- while dataQuality simultaneously listed missing capacityId, sku,
    memoryGB and peakCuPct. An unknown is not a pass.
    """
    findings = findings or []
    overall = max(0, 100 - _penalty(findings))
    groups = {}
    for f in findings:
        groups.setdefault(domain_of(f.get("key")), []).append(f)
    by_domain = {d: max(0, 100 - _penalty(fs)) for d, fs in groups.items()}
    out = {"overall": overall, "byDomain": by_domain}
    gaps = [g for g in (data_quality or []) if g]
    if gaps and not findings:
        # Only when there are NO findings: with findings the score already reflects real problems,
        # and the caveat would just add noise. With none, the 100 is the misleading part.
        out["scoreQualified"] = True
        out["qualification"] = (
            f"Scored on an INCOMPLETE collection ({len(gaps)} data-quality gap(s)): "
            "no findings here means nothing was detectable, not that the estate is healthy.")
    return out
