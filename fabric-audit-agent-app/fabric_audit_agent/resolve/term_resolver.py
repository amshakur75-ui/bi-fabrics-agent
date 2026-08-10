"""term_resolver.py — resolve informal Newell terminology to canonical dataset names.

Python port of the plugin's ``services/term-resolver.ts``. Resolves informal Newell
Z-model terminology to canonical ``Ent-Reporting-*`` dataset names and their Power BI
workspace, using the routing table in ``routing_table.py``.

Matching is three-pass over a normalized form (``normalize_for_matching``):
  0. Canonical name — the input IS an entry's canonical name (``routing_table.canonical_index``).
     Canonical names are not variants, so without this pass a model's own name did not resolve.
  1. Exact match — normalized input equals a normalized variant exactly.
  2. Containment match (only if pass 1 found nothing) — a normalized variant is found
     inside the normalized input as a whole-word-bounded substring. Direction matters:
     the variant must be found INSIDE the input, not the reverse. Variants marked
     ``matchMode: "exact"`` are EXCLUDED from this pass.

Confidence: LOW entries are already excluded at index-construction time (see
routing_table.match_index) — "treat as if it didn't exist".

Ambiguity: a curated ``ambiguousWith`` annotation on a matched variant (currently only
Ent-Reporting-DTC's bare "DTC" and the symmetric Ecomm "online sales"/"digital sales")
produces an ``ambiguous`` result. A generic safety net also handles the theoretical
case of an uncurated lexical collision, with distinguishable reason text.

Result dicts use camelCase keys mirroring the plugin's tool-output shapes, and include
``connectionPath`` on a resolved result when the entry documents one (tightening 26q).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .routing_table import IndexedVariant, all_canonical_names, canonical_index, match_index
from .text_normalize import normalize_for_matching

# Built once at import, mirroring the TS module-level INDEX. LOW-confidence entries are
# already filtered out inside match_index() / canonical_index().
_INDEX: List[IndexedVariant] = match_index()
_CANONICAL_INDEX: List[IndexedVariant] = canonical_index()
_ALL_CANONICAL_NAMES: str = all_canonical_names()


def _build_resolved_message(entry: Dict, variant: Dict) -> str:
    connection_path = entry.get("connectionPath")
    connect = f" Connect via XMLA: {connection_path}" if connection_path is not None else ""
    base = (
        f"Resolved '{variant['text']}' to {entry['canonicalName']} in the "
        f"{entry['pbiWorkspaceName']} workspace"
    )
    if entry.get("confidence") == "MEDIUM":
        return (
            f"{base} (canonical name confidence: medium — verify with the user if this "
            f"affects a high-stakes query).{connect}"
        )
    if not variant.get("verified"):
        return f"{base}. (Matched on an unverified phrasing — verify with the user if uncertain.){connect}"
    return f"{base}.{connect}"


def _build_ambiguous_message(candidates: List[Dict], reason: str, query: str) -> str:
    names = " and ".join(
        f"{c['canonicalName']} ({c['pbiWorkspaceName']} workspace)" for c in candidates
    )
    return f"'{query}' is ambiguous between {names}. {reason} Ask the user which one before querying."


def resolve_term(raw_input: str) -> Dict:
    """Resolve an informal term. Returns a dict with a ``status`` discriminator.

    Statuses: ``resolved`` | ``ambiguous`` | ``no_match``.
    """
    query = raw_input.strip()
    normalized_input = normalize_for_matching(query)

    if normalized_input == "":
        return {
            "status": "no_match",
            "query": query,
            "message": "No term provided — ask the user which model they mean.",
        }

    # Pass 0: the entry's own canonical name. Ahead of the variant passes because a canonical
    # name is the least ambiguous input there is — and because for Ent-Reporting-DTC the
    # containment pass would otherwise match the curated ambiguous bare "DTC" and report the
    # model as ambiguous with itself.
    hits: List[IndexedVariant] = [
        iv for iv in _CANONICAL_INDEX if iv.normalized == normalized_input
    ]

    # Pass 1: exact match.
    if not hits:
        hits = [iv for iv in _INDEX if iv.normalized == normalized_input]

    # Pass 2: containment match, only if pass 1 found nothing. Whole-word-bounded, and
    # matchMode="exact" variants never participate (they had their chance in pass 1).
    if not hits:
        padded = f" {normalized_input} "
        matched: List[IndexedVariant] = []
        for iv in _INDEX:
            if iv.normalized == "":
                continue
            if iv.variant.get("matchMode") == "exact":
                continue
            pattern = re.compile(rf"(^|\s){re.escape(iv.normalized)}(\s|$)")
            if pattern.search(padded):
                matched.append(iv)
        hits = matched

    if not hits:
        return {
            "status": "no_match",
            "query": query,
            "message": (
                f"No canonical model matched '{query}' — ask the user which specific dataset "
                f"they mean. Known models: {_ALL_CANONICAL_NAMES}."
            ),
        }

    # Curated ambiguity: any hit whose matched variant carries ambiguousWith. Checked
    # before the generic safety net, since a curated annotation is a known fact.
    curated = next(
        (h for h in hits if h.variant.get("ambiguousWith")),
        None,
    )
    if curated is not None:
        ambiguous_with = curated.variant["ambiguousWith"]
        primary = {
            "canonicalName": curated.entry["canonicalName"],
            "pbiWorkspaceName": curated.entry["pbiWorkspaceName"],
        }
        others = [
            {"canonicalName": a["canonicalName"], "pbiWorkspaceName": a["pbiWorkspaceName"]}
            for a in ambiguous_with
        ]
        candidates = [primary, *others]
        reason = (
            ambiguous_with[0].get("reason")
            if ambiguous_with
            else None
        ) or "This term is documented as overlapping more than one model."
        return {
            "status": "ambiguous",
            "query": query,
            "ambiguity": {"candidates": candidates, "reason": reason},
            "message": _build_ambiguous_message(candidates, reason, query),
        }

    # Generic safety net: hits resolved to more than one DISTINCT canonical entry with
    # no curated annotation. Not reachable by the locked table today; distinguishable
    # reason text so a future debugging session can tell which path produced the result.
    distinct_entries: List[Dict] = []
    seen_names = set()
    for h in hits:
        name = h.entry["canonicalName"]
        if name not in seen_names:
            seen_names.add(name)
            distinct_entries.append(h.entry)
    if len(distinct_entries) > 1:
        candidates = [
            {"canonicalName": e["canonicalName"], "pbiWorkspaceName": e["pbiWorkspaceName"]}
            for e in distinct_entries
        ]
        reason = (
            "This phrase matched more than one canonical model with no documented relationship "
            "between them — this is an unreviewed lexical collision, not a known data overlap."
        )
        return {
            "status": "ambiguous",
            "query": query,
            "ambiguity": {"candidates": candidates, "reason": reason},
            "message": _build_ambiguous_message(candidates, reason, query),
        }

    # Exactly one distinct entry. Pass 2 only runs when pass 1 is empty, so the first
    # hit is already the exact-match result when one exists.
    best = hits[0]
    result: Dict = {
        "status": "resolved",
        "query": query,
        "canonicalName": best.entry["canonicalName"],
        "pbiWorkspaceName": best.entry["pbiWorkspaceName"],
        "matchedVariant": best.variant["text"],
        "matchedVariantNormalized": best.normalized,
        "variantVerified": bool(best.variant.get("verified")),
        "entryConfidence": best.entry["confidence"],
    }
    connection_path: Optional[str] = best.entry.get("connectionPath")
    if connection_path is not None:
        result["connectionPath"] = connection_path
    result["message"] = _build_resolved_message(best.entry, best.variant)
    return result
