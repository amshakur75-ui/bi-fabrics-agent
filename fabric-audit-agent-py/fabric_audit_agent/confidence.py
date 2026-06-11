"""Confidence for a finding. Port of ``core/confidence.js``.

Deterministic detections = high; meta/errors = low; Claude-enriched = medium.
"""


def score_confidence(finding=None):
    finding = finding or {}
    key = finding.get("key")
    type_ = key.split("::")[0] if isinstance(key, str) else ""
    if type_.startswith("meta."):
        return "low"
    if finding.get("reasonedBy") == "claude":
        return "medium"
    return "high"
