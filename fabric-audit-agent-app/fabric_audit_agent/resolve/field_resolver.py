"""field_resolver.py — resolve field/measure names to authoritative EventText patterns.

Python port of the plugin's ``services/field-resolver.ts``. Loads
``newell-schema.json`` and resolves user-facing field names (measures and columns) to
the canonical DAX and MDX search patterns used inside ``EventText`` entries in Log
Analytics.

EventText search patterns (confirmed from production log samples):
    DAX queries use:  'Table Name'[Field Name]
    MDX queries use:  [Measures].[Field Name]   (measures)
                      [Table Name].[Field Name]  (columns)
Both are searched with ``or`` to catch either query format. Each ``FieldMatch`` carries
a pre-built ``kqlFilter`` ready to embed in a KQL where clause.

Resolution strategy — FOUR passes (tightening 25c), each tried only when the previous
found nothing:
    Pass 1  — exact normalized match (O(1) index lookup).
    Pass 1b — alias expansion (field_aliases.expand_field_alias_variants).
    Pass 1c — schema-linking (schema_link.find_linked_field_names); every suggested name
              re-verified against the production INDEX, capped at 20.
    Pass 2  — containment fallback (min 3-char input, capped at 20).

Disambiguation order when multiple candidates match:
    A. model_hint (from a prior resolve_term) -> narrows to one model -> HIGH.
    B. measure preference -> if exactly one candidate is a measure -> MEDIUM.
    C. still ambiguous -> return all candidates + a combinedKqlFilter (AuthoritativeFilter)
       built from the FULL candidate list, so an all-models search is never truncated.

``resolve_field_usage`` is the seam-closing entry point: it resolves a field to an
authoritative filter and hands it to the usage builder, returning a complete,
provenance-tracked, ready-to-run query. A caller cannot inject a filter (the builder
requires a branded ``AuthoritativeFilter``) nor introduce an arbitrary column (rejected
by the safe enum).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import newell_schema_path as _default_schema_path
from .field_aliases import expand_field_alias_variants
from .schema_link import SchemaLinkIndex
from .text_normalize import normalize_for_matching
from .usage_query_builder import (
    AuthoritativeFilter,
    BuildUsageQueryResult,
    EqualityFilter,
    build_usage_query,
    mint_authoritative_filter,
)


def _build_kql_filter(dax_pattern: str, mdx_pattern: str) -> str:
    return f'EventText contains "{dax_pattern}" or EventText contains "{mdx_pattern}"'


class FieldResolver:
    """Loads ``newell-schema.json`` and resolves field/measure names.

    Degraded mode: if the schema fails to load, ``resolve_field`` returns an
    ``unavailable`` result rather than raising.
    """

    def __init__(
        self,
        schema_path: Optional[Path] = None,
        schema_link: Optional[SchemaLinkIndex] = None,
        *,
        load: bool = True,
    ) -> None:
        self._schema_path: Path = Path(schema_path) if schema_path is not None else _default_schema_path()
        self._schema_link = schema_link
        # normalized field name -> list of FieldMatch dicts
        self._index: Optional[Dict[str, List[Dict]]] = None
        self._load_error: Optional[str] = None
        if load:
            self.load()

    # ── Loader ────────────────────────────────────────────────────────────────────
    def load(self) -> None:
        try:
            with open(self._schema_path, "r", encoding="utf-8") as f:
                schema = json.load(f)
            index: Dict[str, List[Dict]] = {}

            def add_field(model_name: str, table_name: str, field: Dict, field_type: str) -> None:
                entry = {
                    "model": model_name,
                    "table": table_name,
                    "fieldName": field["name"],
                    "fieldType": field_type,
                    "displayFolder": field.get("displayFolder"),
                    "daxPattern": field["patterns"]["dax"],
                    "mdxPattern": field["patterns"]["mdx"],
                    "kqlFilter": _build_kql_filter(field["patterns"]["dax"], field["patterns"]["mdx"]),
                }
                key = normalize_for_matching(field["name"])
                index.setdefault(key, []).append(entry)

            for model_name, model_data in schema["models"].items():
                for table_name, table_data in model_data["tables"].items():
                    for col in table_data.get("columns", []):
                        add_field(model_name, table_name, col, "column")
                    for meas in table_data.get("measures", []):
                        add_field(model_name, table_name, meas, "measure")

            self._index = index
            sys.stderr.write(
                f"FieldSchema loaded: {len(index)} unique field names across "
                f"{len(schema['models'])} models.\n"
            )
        except Exception as err:
            self._load_error = str(err)
            sys.stderr.write(f"FieldSchema load error: {self._load_error}\n")

    def is_available(self) -> bool:
        return self._index is not None

    # ── Combined authoritative filter for the ambiguous case ───────────────────────
    def _build_combined_kql_filter(self, candidates: Sequence[Dict]) -> AuthoritativeFilter:
        """One authoritative filter covering EVERY candidate (dedup identical patterns).

        Built ONLY from the authoritative dax/mdx strings already on each FieldMatch —
        never from free text — then minted as an ``AuthoritativeFilter``. Operates on the
        FULL candidate list, so no model is silently omitted from the search.
        """
        seen = set()
        clauses: List[str] = []
        for c in candidates:
            for pattern in (c["daxPattern"], c["mdxPattern"]):
                if pattern not in seen:
                    seen.add(pattern)
                    clauses.append(f'EventText contains "{pattern}"')
        return mint_authoritative_filter("\n    or ".join(clauses))

    # ── Resolver ────────────────────────────────────────────────────────────────
    def resolve_field(self, field_name: str, model_hint: Optional[str] = None) -> Dict:
        """Resolve a field/measure name. Returns a dict with a ``status`` discriminator.

        Statuses: ``resolved`` | ``ambiguous`` | ``no_match`` | ``unavailable``.
        """
        query = field_name.strip()

        if self._index is None:
            reason = self._load_error or (
                "Field schema not loaded — load() was not called at startup."
            )
            return {"status": "unavailable", "reason": reason, "message": f"Field lookup unavailable: {reason}"}

        normalized = normalize_for_matching(query)
        if normalized == "":
            return {
                "status": "no_match",
                "query": query,
                "message": "No field name provided — ask the user which field or measure they mean.",
            }

        # Pass 1: exact normalized match — O(1).
        candidates: List[Dict] = list(self._index.get(normalized, []))
        matched_via_alias = False
        matched_via_schema_link = False

        # Pass 1b: alias/synonym match — only when Pass 1 found nothing.
        if not candidates:
            for variant in expand_field_alias_variants(normalized):
                hit = self._index.get(variant, [])
                if hit:
                    candidates = list(hit)
                    matched_via_alias = True
                    break

        # Pass 1c: schema-linking candidate retrieval — only when 1 and 1b found nothing.
        if not candidates and self._schema_link is not None:
            linked_names = self._schema_link.find_linked_field_names(
                normalized, expand_field_alias_variants(normalized)
            )
            gathered: List[Dict] = []
            for name in linked_names:
                for e in self._index.get(normalize_for_matching(name), []):
                    gathered.append(e)
            if 0 < len(gathered) <= 20:
                candidates = gathered
                matched_via_schema_link = True

        # Pass 2: containment fallback — only when 1/1b/1c found nothing, min 3 chars.
        if not candidates and len(normalized) >= 3:
            gathered = []
            for key, entries in self._index.items():
                if normalized in key:
                    gathered.extend(entries)
            candidates = gathered[:20]

        if not candidates:
            return {
                "status": "no_match",
                "query": query,
                "message": (
                    f"No field matched '{query}'. Check spelling, or ask the user to clarify the "
                    "field name. Partial terms may help — e.g. 'invoice' instead of 'invoice quantity'."
                ),
            }

        # Single result: unambiguous.
        if len(candidates) == 1:
            m = candidates[0]
            confidence = "MEDIUM" if (matched_via_alias or matched_via_schema_link) else "HIGH"
            if matched_via_alias:
                message = (
                    f"Resolved '{query}' to {m['fieldType']} '{m['fieldName']}' in table "
                    f"'{m['table']}' ({m['model']}), matched via abbreviation/synonym expansion — "
                    f"verify with user if uncertain. Use this KQL filter: {m['kqlFilter']}"
                )
            elif matched_via_schema_link:
                message = (
                    f"Resolved '{query}' to {m['fieldType']} '{m['fieldName']}' in table "
                    f"'{m['table']}' ({m['model']}), matched via schema metadata — verify with user "
                    f"if uncertain. Use this KQL filter: {m['kqlFilter']}"
                )
            else:
                message = (
                    f"Resolved '{query}' to {m['fieldType']} '{m['fieldName']}' in table "
                    f"'{m['table']}' ({m['model']}). Use this KQL filter: {m['kqlFilter']}"
                )
            return {"status": "resolved", "match": m, "confidence": confidence, "message": message}

        # Multiple candidates — apply disambiguation in priority order.

        # Step A: model_hint.
        if model_hint is not None and model_hint.strip() != "":
            hint = model_hint.strip()
            hinted = [c for c in candidates if c["model"] == hint]
            if len(hinted) == 1:
                m = hinted[0]
                return {
                    "status": "resolved",
                    "match": m,
                    "confidence": "HIGH",
                    "message": (
                        f"Resolved '{query}' to {m['fieldType']} '{m['fieldName']}' in table "
                        f"'{m['table']}' ({m['model']}, narrowed by model context from resolve_term). "
                        f"Use this KQL filter: {m['kqlFilter']}"
                    ),
                }
            if len(hinted) > 1:
                fields_desc = ", ".join(f"'{c['table']}'[{c['fieldName']}] ({c['fieldType']})" for c in hinted)
                return {
                    "status": "ambiguous",
                    "candidates": hinted,
                    "combinedKqlFilter": self._build_combined_kql_filter(hinted),
                    "message": (
                        f"'{query}' matched {len(hinted)} fields in {hint}: {fields_desc}. A combined "
                        "filter covering all of them is available in combinedKqlFilter, or ask the user "
                        "which table they mean."
                    ),
                }
            # Hint found 0 matches in the specified model — fall through using the full
            # candidate list, not the filtered one.

        # Step B: measure preference — exactly one candidate is a measure.
        measures_only = [c for c in candidates if c["fieldType"] == "measure"]
        if len(measures_only) == 1:
            m = measures_only[0]
            return {
                "status": "resolved",
                "match": m,
                "confidence": "MEDIUM",
                "message": (
                    f"Resolved '{query}' to measure '{m['fieldName']}' in table '{m['table']}' "
                    f"({m['model']}). Multiple fields share this name but this is the only measure — "
                    f"verify with user if uncertain. Use this KQL filter: {m['kqlFilter']}"
                ),
            }

        # Step C: still ambiguous — return candidates (capped at 10 for readability).
        # combinedKqlFilter is built from the FULL candidate list, never the capped list.
        distinct_models = len({c["model"] for c in candidates})
        shown = candidates[:10]
        overflow = f" and {len(candidates) - 10} more" if len(candidates) > 10 else ""
        shown_desc = ", ".join(
            f"'{c['table']}'[{c['fieldName']}] in {c['model']} ({c['fieldType']})" for c in shown
        )
        return {
            "status": "ambiguous",
            "candidates": shown,
            "combinedKqlFilter": self._build_combined_kql_filter(candidates),
            "message": (
                f"'{query}' matched {len(candidates)} fields across {distinct_models} model(s): "
                f"{shown_desc}{overflow}. combinedKqlFilter searches all matching models at once. "
                "To narrow to one model instead, call resolve_term first, then call resolve_field "
                "again with the model_hint parameter."
            ),
        }

    # ── Field-usage seam ──────────────────────────────────────────────────────────
    def resolve_field_usage(
        self,
        field_name: str,
        group_by: Sequence[str],
        timespan: str,
        model_hint: Optional[str] = None,
        equality_filters: Optional[Sequence[EqualityFilter]] = None,
        top_n: Optional[int] = None,
        title: Optional[str] = None,
    ) -> Dict:
        """Resolve a field and, when it resolves, return a ready-to-run usage query.

        Statuses: ``query_ready`` | ``invalid_request`` | ``no_match`` | ``unavailable``.
        The ``invalid_request`` status (tightening 26s) means the field WAS found but the
        builder rejected the caller's groupBy/timespan/etc. — distinct from ``no_match``
        (wrong field name). No query is ever fabricated on no_match/unavailable.
        """
        resolved = (
            self.resolve_field(field_name, model_hint)
            if model_hint is not None
            else self.resolve_field(field_name)
        )

        if resolved["status"] == "resolved":
            filter_ = mint_authoritative_filter(resolved["match"]["kqlFilter"])
            resolution = {
                "status": "resolved",
                "confidence": resolved["confidence"],
                "match": resolved["match"],
            }
        elif resolved["status"] == "ambiguous":
            filter_ = resolved["combinedKqlFilter"]
            models = list(dict.fromkeys(c["model"] for c in resolved["candidates"]))
            resolution = {
                "status": "ambiguous",
                "candidateCount": len(resolved["candidates"]),
                "models": models,
            }
        else:
            # no_match / unavailable: never fabricate a query.
            return {"status": resolved["status"], "message": resolved["message"]}

        built: BuildUsageQueryResult = build_usage_query(
            authoritative_filter=filter_,
            group_by=group_by,
            timespan=timespan,
            equality_filters=equality_filters,
            top_n=top_n if top_n is not None else 5,
            title=title,
        )

        if not built.ok:
            return {
                "status": "invalid_request",
                "reason": built.reason,
                "resolution": resolution,
                "message": f"Field resolved, but the usage query could not be built: {built.reason}",
            }

        if resolution["status"] == "resolved":
            scope_note = (
                f"Query targets '{resolution['match']['model']}' ({resolution['confidence']} confidence)."
            )
        else:
            scope_note = (
                f"Query searches all {resolution['candidateCount']} matching field(s) across "
                f"{len(resolution['models'])} model(s): {', '.join(resolution['models'])}."
            )
        retention_note = f" Note: {built.retention_warning}" if built.retention_warning is not None else ""

        return {
            "status": "query_ready",
            "query": built.query,
            "provenance": built.provenance,
            "resolution": resolution,
            "message": (
                f"Ready-to-run usage query built from authoritative field patterns. {scope_note}{retention_note}"
            ),
        }


_DEFAULT_FIELD_RESOLVER: Optional[FieldResolver] = None


def default_field_resolver() -> FieldResolver:
    """Return a cached ``FieldResolver`` with schema-link Pass 1c wired to the catalog."""
    global _DEFAULT_FIELD_RESOLVER
    if _DEFAULT_FIELD_RESOLVER is None:
        from .catalog import default_catalog

        link = SchemaLinkIndex(catalog=default_catalog())
        _DEFAULT_FIELD_RESOLVER = FieldResolver(schema_link=link)
    return _DEFAULT_FIELD_RESOLVER
