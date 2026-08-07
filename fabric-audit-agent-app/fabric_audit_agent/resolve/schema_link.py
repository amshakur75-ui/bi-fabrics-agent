"""schema_link.py — deterministic schema-linking retrieval (field-resolver Pass 1c).

Python port of the plugin's ``services/schema-link.ts``. A metadata-driven retrieval
layer over the Newell field catalog (20k+ field records). It NEVER produces a
``FieldMatch`` and NEVER touches DAX/MDX/KQL generation — it only suggests candidate
FIELD NAME STRINGS. Every candidate name must still be independently re-verified through
the production field-resolver INDEX before it can become a real match; a catalog-only
name that doesn't exist in that INDEX is silently discarded, never fabricated.

Index source, in preference order:
  1. The pre-built ``data/plugin/catalog`` search index via ``catalog.py`` (normal path).
  2. Legacy fallback: direct parse of ``enriched-field-catalog.json`` when the built
     catalog is absent.

Tokenization is deliberately conservative: lowercase; split on whitespace/underscore/
hyphen only. Only ``field`` and ``sourceColumn`` are tokenized.

The FOUR Pass-1c guardrails (tightening 26c), faithfully ported:
  1. Single-token queries must be >= 4 chars (SINGLE_TOKEN_MIN_LENGTH). Below that,
     Pass 1c returns empty and the resolver falls through to containment ("qty" is
     ALIAS_MAP's job, run before this).
  2. Multi-token queries use AND-semantics (intersection of per-token field-name sets).
  3. Runaway sanity ceiling of 500 (RUNAWAY_SANITY_CEILING); only for pathological cases.
  4. Alias-expanded variants are tried FIRST; the raw query last. An alias hit
     short-circuits so a worse containment match never wins.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from .catalog import Catalog

_TOKEN_SPLIT = re.compile(r"[\s_-]+")

# Guardrails (v1, conservative).
SINGLE_TOKEN_MIN_LENGTH = 4
RUNAWAY_SANITY_CEILING = 500  # catches only pathological single-token cases


def _tokenize(value: str) -> List[str]:
    return [t for t in _TOKEN_SPLIT.split(value.lower()) if t]


class SchemaLinkIndex:
    """token -> set of distinct field NAME STRINGS, with the 4 Pass-1c guardrails.

    Prefers the built catalog's token index; falls back to parsing the raw
    ``enriched-field-catalog.json``. On any failure the index stays ``None`` and
    ``find_linked_field_names`` returns ``[]`` — Pass 1c becomes a no-op and the resolver
    falls through to containment exactly as before. Failure here can never break existing
    behaviour.
    """

    def __init__(
        self,
        catalog: Optional[Catalog] = None,
        legacy_catalog_path: Optional[Path] = None,
        *,
        load: bool = True,
    ) -> None:
        self._catalog = catalog
        self._legacy_path = legacy_catalog_path
        self._token_index: Optional[Dict[str, Set[str]]] = None
        if load:
            self.load()

    def load(self) -> None:
        # Preferred path: the pre-built catalog search index (same tokenizer over the
        # same inputs), so Pass 1c behaviour is identical without re-parsing the 14MB
        # raw catalog.
        catalog = self._catalog
        if catalog is not None and catalog.is_available():
            idx = catalog.get_field_name_token_index()
            if idx is not None and len(idx) > 0:
                self._token_index = idx
                sys.stderr.write(
                    f"SchemaLinkIndex loaded from built catalog: {len(idx)} distinct tokens.\n"
                )
                return
        # Legacy fallback: parse enriched-field-catalog.json directly.
        if self._legacy_path is None:
            return
        try:
            with open(self._legacy_path, "r", encoding="utf-8") as f:
                records = json.load(f)
            index: Dict[str, Set[str]] = {}
            for rec in records:
                field_name = rec.get("field")
                if not field_name:
                    continue
                tokens: Set[str] = set(_tokenize(field_name))
                source_column = rec.get("sourceColumn")
                if source_column:
                    tokens.update(_tokenize(source_column))
                for token in tokens:
                    index.setdefault(token, set()).add(field_name)
            self._token_index = index
            sys.stderr.write(
                f"SchemaLinkIndex loaded (legacy): {len(index)} distinct tokens from "
                f"{len(records)} catalog records.\n"
            )
        except Exception as err:  # non-fatal — Pass 1c disabled
            sys.stderr.write(
                f"SchemaLinkIndex load error (non-fatal, Pass 1c disabled): {err}\n"
            )

    def is_available(self) -> bool:
        return self._token_index is not None

    def find_linked_field_names(
        self, normalized_query: str, alias_variants: Optional[Sequence[str]] = None
    ) -> List[str]:
        """Return candidate field NAME STRINGS from the token index.

        Returns ``[]`` if the index isn't loaded, if guardrail caps are exceeded, or if
        nothing matches — all meaning "Pass 1c found nothing usable, proceed to
        containment fallback" from the caller's perspective.
        """
        if self._token_index is None:
            return []

        # Guardrail 4: try alias-expanded variants FIRST, raw query LAST.
        queries_to_try: List[str] = list(alias_variants or []) + [normalized_query]

        for q in queries_to_try:
            tokens = _tokenize(q)
            if not tokens:
                continue

            if len(tokens) == 1:
                token = tokens[0]
                # Guardrail 1: single-token min length.
                if len(token) < SINGLE_TOKEN_MIN_LENGTH:
                    continue
                hits = self._token_index.get(token)
                if not hits:
                    continue
                # Guardrail 3: runaway sanity ceiling.
                if len(hits) > RUNAWAY_SANITY_CEILING:
                    continue  # pathological only
                return list(hits)

            # Guardrail 2: multi-token AND-semantics — intersect per-token sets.
            intersection: Optional[Set[str]] = None
            for token in tokens:
                hits = self._token_index.get(token)
                if not hits:
                    intersection = set()
                    break
                if intersection is None:
                    intersection = set(hits)
                else:
                    intersection &= hits
            if (
                intersection is not None
                and len(intersection) > 0
                and len(intersection) <= RUNAWAY_SANITY_CEILING
            ):
                return list(intersection)

        return []
