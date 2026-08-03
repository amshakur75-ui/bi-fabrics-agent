"""Confidence for a finding. Port of ``core/confidence.js``.

Deterministic detections = high; meta/errors = low; Claude-enriched = medium.

B5 -- ClaimConfidence enum added to enforce typed confidence labels at the code level,
complementing gates.py's STOP gates. The distinction matters: gates block specific claims from
being made at all; ClaimConfidence labels what was proven vs. inferred for claims that do proceed.
"""
from enum import Enum


class ClaimConfidence(str, Enum):
    """Typed confidence labels for individual claims.

    These align with the system prompt's validated/likely/inconclusive vocabulary.
    Using a typed enum rather than bare strings means a wrong label is a TypeError, not a
    silent slip -- and makes it impossible to call a figure "validated" just because a query
    ran (see SP2 in the system prompt).

    ``PROXY`` is a fourth value for per-user / per-item monitored-CU figures that are
    structurally a CPU-time proxy and can never be "validated" as authoritative capacity CU.
    The gates.py TRUE_CU_PER_USER_PERMANENTLY_BLOCKED gate blocks these claims at the tool
    level; ClaimConfidence.PROXY labels them if they surface in a context where the gate
    doesn't apply (e.g. an informational table).
    """
    VALIDATED = "validated"       # formula verified against documented source / gate result
    LIKELY = "likely"             # consistent with data but not uniquely determined
    INCONCLUSIVE = "inconclusive" # insufficient evidence to favour any cause
    PROXY = "proxy"               # monitored CPU-time proxy -- never billed capacity CU


def claim_confidence(presence):
    """Return a ClaimConfidence value based on what was actually present in the data.

    ``presence`` is a dict of bool flags:
    - ``is_user_attribution`` -- True if the figure is per-user / per-item from monitored
      telemetry (CpuTimeMs proxy); these can never be VALIDATED as billed CU.
    - ``gate_passed`` -- True if a deterministic STOP gate (gates.py) passed for this claim.
    - ``formula_verified`` -- True if the formula was cross-checked against the Capacity
      Metrics app, a documented source, or the formula-validation session (GAPS Section 12).
    - ``has_data`` -- True if at least some relevant query rows were returned.

    Rules (in precedence order):
    1. User attribution figures always return PROXY regardless of other flags.
    2. Both gate_passed AND formula_verified required for VALIDATED.
    3. Any data at all -> LIKELY.
    4. No data -> INCONCLUSIVE.
    """
    if presence.get("is_user_attribution"):
        return ClaimConfidence.PROXY
    if presence.get("gate_passed") and presence.get("formula_verified"):
        return ClaimConfidence.VALIDATED
    if presence.get("has_data"):
        return ClaimConfidence.LIKELY
    return ClaimConfidence.INCONCLUSIVE


def score_confidence(finding=None):
    """Legacy per-finding confidence scorer (string-valued). Kept for backward compatibility.

    Deterministic detections = high; meta/errors = low; Claude-enriched = medium.
    New code should prefer claim_confidence() + ClaimConfidence for individual claims.
    """
    finding = finding or {}
    key = finding.get("key")
    type_ = key.split("::")[0] if isinstance(key, str) else ""
    if type_.startswith("meta."):
        return "low"
    if finding.get("reasonedBy") == "claude":
        return "medium"
    return "high"
