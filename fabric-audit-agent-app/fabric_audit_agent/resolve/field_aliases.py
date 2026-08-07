"""field_aliases.py — small semantic alias layer for the field resolver.

Python port of the plugin's ``services/field-aliases.ts``. Maps common abbreviations,
business shorthand, and simple pluralization to the normalized tokens actually used in
schema field names, so a query like "qty" or "cust" can reach a field named
"Quantity" / "Customer" without an exact or containment match on the raw input.

Scope is intentionally narrow: a static abbreviation table plus a trailing-"s"
pluralization strip. No fuzzy matching, no NLP, no scoring model — a lookup table, not
a framework.

Consumed by ``field_resolver.py`` as Pass 1b: an additional candidate-generation pass
that runs only when the exact-match pass finds nothing, tried before the raw-containment
fallback so a direct alias hit ranks above a coincidental containment hit.

PORT NOTE (spec discrepancy): tightening.md Parts intro and 25c describe "ALIAS_MAP
(35 entries)", but the authoritative ``field-aliases.ts`` — and the enumerated list in
25c itself — contain exactly **27** entries. The real source file is authoritative, so
this port has 27 entries, transcribed verbatim. The "35" figure in the plan is
inaccurate; no entries were invented to reach it.
"""
from __future__ import annotations

from typing import Dict, List

# Abbreviation / synonym -> canonical token, both sides already normalized (lowercase,
# single-space-collapsed) to match normalize_for_matching's output. Keys and values are
# single words; expand_field_alias_variants applies this word-by-word across the whole
# normalized query. Transcribed verbatim from field-aliases.ts (27 entries).
ALIAS_MAP: Dict[str, str] = {
    "qty": "quantity",
    "amt": "amount",
    "cust": "customer",
    "custs": "customer",
    "qtys": "quantity",
    "amts": "amount",
    "desc": "description",
    "descr": "description",
    "num": "number",
    "nbr": "number",
    "addr": "address",
    "invc": "invoice",
    "inv": "invoice",
    "ord": "order",
    "qy": "quantity",
    "pct": "percent",
    "avg": "average",
    "tot": "total",
    "disc": "discount",
    "vend": "vendor",
    "whs": "warehouse",
    "wh": "warehouse",
    "sku": "sku",  # identity entry — keeps sku from being stripped by pluralization below
    "rev": "revenue",
    "mgr": "manager",
    "dept": "department",
    "qtr": "quarter",
}


def strip_trailing_s(word: str) -> str:
    """Minimal pluralization handling.

    Strip a single trailing "s" for words longer than 3 chars, so "customers" ->
    "customer", "orders" -> "order". Deliberately does NOT handle "-ies"/"-es" edge
    cases (out of scope, low value relative to the risk of over-stripping short/legit
    words) and never strips a "ss" ending.
    """
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def expand_field_alias_variants(normalized_query: str) -> List[str]:
    """Return alias variants of an already-normalized query, in priority order.

    Does not include the original input — the caller already tries that via the
    exact-match pass. Mirrors ``expandFieldAliasVariants`` in field-aliases.ts:
      1. Whole-query variant: map each word through ALIAS_MAP (or leave as-is), then
         strip a trailing "s" on any word not already mapped.
      2. Plain pluralization-only variant (no alias substitution), in case the aliasing
         above did not fire but a simple plural strip would still land an exact match.

    Insertion order is preserved and duplicates are dropped, so a variant identical to
    the whole-query mapping is not repeated by the pluralization-only pass.
    """
    words = [w for w in normalized_query.split(" ") if w]
    if not words:
        return []

    variants: List[str] = []

    def _add(candidate: str) -> None:
        if candidate != normalized_query and candidate not in variants:
            variants.append(candidate)

    # 1. Whole-query alias + pluralization variant.
    mapped_words = [ALIAS_MAP.get(w, strip_trailing_s(w)) for w in words]
    _add(" ".join(mapped_words))

    # 2. Plain pluralization-only variant.
    _add(" ".join(strip_trailing_s(w) for w in words))

    return variants
