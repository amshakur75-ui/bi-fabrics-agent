"""Classify a natural-language question as targeting KQL, SQL, or DAX. Pure stdlib.

More robust than a simple keyword-in-question approach (N22 flagged that as fragile):
uses weighted indicator groups with confidence scoring. Each group carries a weight; the
target with the highest weighted score wins. When no indicators match, defaults to KQL
(the agent's primary telemetry surface). When scores are tied, returns the one with the
more specific indicator match (DAX > SQL > KQL, since KQL is the broadest default).

The classifier is intentionally rule-based (no LLM call) so it can run synchronously,
deterministically, and without network I/O -- the LLM route-selection happens upstream
in the chat layer (the MCP host), not here.
"""

import re

# Each indicator group: (compiled regex pattern, weight). Higher weight = stronger signal.
# Patterns are case-insensitive and word-boundary anchored where appropriate.

_KQL_INDICATORS = [
    (re.compile(r"\b(?:kusto|kql|eventhouse)\b", re.I), 3.0),
    (re.compile(r"\blog(?:\s+analytics|s)?\b", re.I), 2.0),
    (re.compile(r"\b(?:events?|traces?|telemetry)\b", re.I), 1.5),
    (re.compile(r"\b(?:timegenerated|cputimems|durationms)\b", re.I), 3.0),
    (re.compile(r"\b(?:ago|summarize|extend|where.*ago|render)\b", re.I), 2.0),
    (re.compile(r"\boperationname\b", re.I), 2.0),
    (re.compile(r"\b(?:capacity\s*metrics|capacity\s*events)\b", re.I), 2.0),
    (re.compile(r"\b(?:adx|azure\s*data\s*explorer)\b", re.I), 3.0),
    (re.compile(r"\bworkspace\s*monitoring\b", re.I), 2.0),
]

_SQL_INDICATORS = [
    (re.compile(r"\b(?:lakehouse|warehouse|sql\s*endpoint)\b", re.I), 3.0),
    (re.compile(r"\b(?:sql|t-sql|transact-sql)\b", re.I), 3.0),
    (re.compile(r"\b(?:table|column|row|schema)\b", re.I), 1.0),
    (re.compile(r"\b(?:join|group\s*by|having|where|order\s*by)\b", re.I), 1.0),
    (re.compile(r"\b(?:delta\s*table|parquet|onelake)\b", re.I), 2.5),
    (re.compile(r"\b(?:stored\s*procedure|view|index)\b", re.I), 2.0),
    (re.compile(r"\b(?:data\s*warehouse|dwh|star\s*schema|fact\s*table|dim(?:ension)?\s*table)\b", re.I), 2.5),
]

_DAX_INDICATORS = [
    (re.compile(r"\b(?:dax|measure|calculated\s*column)\b", re.I), 3.0),
    (re.compile(r"\b(?:semantic\s*model|dataset|power\s*bi\s*model)\b", re.I), 2.5),
    (re.compile(r"\b(?:report|visual|dashboard|power\s*bi\s*report)\b", re.I), 1.5),
    (re.compile(r"\b(?:evaluate|calculatetable|summarizecolumns|addcolumns)\b", re.I), 3.0),
    (re.compile(r"\b(?:xmla|adomd|ssas|tabular)\b", re.I), 3.0),
    (re.compile(r"\b(?:relationship|star\s*schema|model)\b", re.I), 1.0),
    (re.compile(r"\b(?:slicer|filter\s*context|row\s*context)\b", re.I), 2.5),
    (re.compile(r"\bCAPTION\b", re.I), 0),  # common DAX DEFINE keyword
    (re.compile(r"\b(?:TOPN|RANKX|EARLIER|ALLSELECTED|TREATAS)\b", re.I), 3.0),
]


def _score(question, indicators):
    """Sum the weighted matches for a set of indicators against a question."""
    total = 0.0
    for pattern, weight in indicators:
        if pattern.search(question):
            total += weight
    return total


def classify(question):
    """Classify a natural-language question as targeting KQL, SQL, or DAX.

    Returns ``{"target": "kql"|"sql"|"dax", "confidence": float, "reason": str}``.

    ``confidence`` is a normalized 0.0-1.0 value:
    - > 0.7: strong signal (explicit mention of the target system)
    - 0.3-0.7: moderate signal (domain-adjacent indicators)
    - < 0.3: weak signal (defaulting, no strong indicators)
    """
    q = str(question)

    kql_score = _score(q, _KQL_INDICATORS)
    sql_score = _score(q, _SQL_INDICATORS)
    dax_score = _score(q, _DAX_INDICATORS)

    total = kql_score + sql_score + dax_score

    if total == 0:
        # No indicators at all -- default to KQL (the agent's primary surface)
        return {
            "target": "kql",
            "confidence": 0.2,
            "reason": "no domain indicators found; defaulting to KQL (primary telemetry surface)",
        }

    # Pick the highest-scoring target; break ties in favor of the more specific surface
    # (DAX > SQL > KQL, since KQL is the broadest default)
    candidates = [
        ("kql", kql_score),
        ("sql", sql_score),
        ("dax", dax_score),
    ]
    # Sort by score descending, then by specificity (dax=0, sql=1, kql=2)
    specificity = {"dax": 0, "sql": 1, "kql": 2}
    candidates.sort(key=lambda x: (-x[1], specificity[x[0]]))

    winner, winner_score = candidates[0]
    runner_up_score = candidates[1][1]

    # Confidence is based on the winner's share of the total score + margin over runner-up
    share = winner_score / total
    margin = (winner_score - runner_up_score) / total if total > 0 else 0
    confidence = min(1.0, share * 0.7 + margin * 0.3 + (0.1 if winner_score >= 3 else 0))
    confidence = round(confidence, 2)

    # Build reason
    if winner_score >= 3:
        strength = "strong"
    elif winner_score >= 1.5:
        strength = "moderate"
    else:
        strength = "weak"

    reason = (
        f"{strength} {winner.upper()} signal (score {winner_score:.1f} vs "
        f"runner-up {runner_up_score:.1f})"
    )

    return {"target": winner, "confidence": confidence, "reason": reason}
