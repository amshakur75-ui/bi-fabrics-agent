"""catalog.py — runtime loader for the pre-built Newell field catalog.

Python port of the plugin's ``services/catalog.ts``. Loads the pre-built field catalog
produced by ``build-field-catalog.cjs`` (``data/plugin/catalog/``: manifest.json,
search-index.json, models/<Canonical>.json).

Design (per the large-catalog integration blueprint):
  * Startup loads ONLY the manifest + search index (~2.5MB) — never the 14MB raw
    catalog. Per-model record files lazy-load on first touch and memoize in a dict.
  * The search-index token space is byte-compatible with ``schema_link.tokenize``
    (lowercase; split on whitespace/underscore/hyphen; over field + sourceColumn), so
    schema-link can consume it with identical Pass 1c behaviour.
  * Degraded mode: any missing/corrupt file leaves the catalog unloaded; every accessor
    then reports unavailability instead of throwing. Failure here must never break field
    resolution (mirrors the schema-link contract).

MODEL_MAP invariant (tightening 25h): a helper is provided to assert every catalog model
name maps to a known routing ``catalogModelName`` — fail loud, never guess.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

from . import catalog_dir as _default_catalog_dir

# tokenizer: lowercase, split on whitespace/_/-, drop empties. Mirrors
# schema-link.ts / build-field-catalog.cjs tokenize() exactly.
_TOKEN_SPLIT = re.compile(r"[\s_-]+")


def tokenize(value: str) -> List[str]:
    """Lowercase then split on whitespace/underscore/hyphen; drop empty tokens."""
    return [t for t in _TOKEN_SPLIT.split(value.lower()) if t]


class Catalog:
    """Lazy, degraded-mode loader for the pre-built field catalog.

    Construct with a catalog directory (defaults to ``data/plugin/catalog``). Loading is
    attempted eagerly for the small manifest + search index; per-model records load and
    memoize on first access. Any failure leaves the catalog unavailable — accessors
    return ``None`` rather than raising.
    """

    def __init__(self, directory: Optional[Path] = None, *, load: bool = True) -> None:
        self._dir: Optional[Path] = Path(directory) if directory is not None else _default_catalog_dir()
        self._manifest: Optional[Dict] = None
        self._search_index: Optional[Dict] = None
        self._model_cache: Dict[str, List[Dict]] = {}
        self._field_name_index: Optional[Dict[str, Set[str]]] = None
        self._load_error: Optional[str] = None
        if load:
            self.load()

    # ── Loader ────────────────────────────────────────────────────────────────────
    def load(self) -> None:
        """Load manifest + search index. Non-fatal on failure (leaves catalog degraded)."""
        directory = self._dir
        try:
            if directory is None:
                raise FileNotFoundError("no catalog directory configured")
            with open(directory / "manifest.json", "r", encoding="utf-8") as f:
                manifest = json.load(f)
            with open(directory / "search-index.json", "r", encoding="utf-8") as f:
                index = json.load(f)
            if not isinstance(index.get("fields"), list) or not isinstance(index.get("postings"), dict):
                raise ValueError("search-index.json has unexpected shape")
            self._manifest = manifest
            self._search_index = index
            self._field_name_index = None  # rebuilt lazily
        except Exception as err:  # degraded mode — never raise
            self._load_error = str(err)
            self._manifest = None
            self._search_index = None
            sys.stderr.write(
                f"FieldCatalog load error (non-fatal, catalog tools degraded): {self._load_error}\n"
            )

    # ── Accessors ────────────────────────────────────────────────────────────────
    def is_available(self) -> bool:
        return self._search_index is not None

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    def get_manifest(self) -> Optional[Dict]:
        return self._manifest

    def get_field_name_token_index(self) -> Optional[Dict[str, Set[str]]]:
        """token -> set of distinct FIELD NAMES. Built once from the search index, memoized.

        Same shape ``schema_link`` builds internally, so schema-link can consume it
        directly with identical Pass 1c behaviour.
        """
        if self._search_index is None:
            return None
        if self._field_name_index is not None:
            return self._field_name_index
        fields = self._search_index["fields"]
        out: Dict[str, Set[str]] = {}
        for token, ids in self._search_index["postings"].items():
            names: Set[str] = set()
            for fid in ids:
                if 0 <= fid < len(fields):
                    f = fields[fid]
                    if f:
                        names.add(f["f"])
            out[token] = names
        self._field_name_index = out
        return out

    def search_fields(
        self, query: str, model: Optional[str] = None, limit: int = 10
    ) -> Optional[Dict]:
        """Fuzzy field discovery over the whole catalog.

        OR-semantics with score = matched-token count, so multi-token queries rank
        AND-matches first without excluding partial matches. Returns
        ``{"hits": [...], "totalMatches": N}`` (hits capped at ``min(limit, 25)``), or
        ``None`` when the catalog is unavailable.
        """
        if self._search_index is None:
            return None
        limit = min(limit, 25)
        tokens = list(dict.fromkeys(tokenize(query)))  # dedup, preserve order
        if not tokens:
            return {"hits": [], "totalMatches": 0}

        fields = self._search_index["fields"]
        postings = self._search_index["postings"]
        scores: Dict[int, int] = {}
        for tok in tokens:
            ids = postings.get(tok)
            if not ids:
                continue
            for fid in ids:
                scores[fid] = scores.get(fid, 0) + 1

        entries = list(scores.items())
        if model is not None:
            entries = [
                (fid, sc)
                for (fid, sc) in entries
                if 0 <= fid < len(fields) and fields[fid] and fields[fid]["m"] == model
            ]
        total_matches = len(entries)
        # sort by score desc, then field id asc (stable tie-break) — mirrors TS.
        entries.sort(key=lambda e: (-e[1], e[0]))

        hits: List[Dict] = []
        for fid, score in entries[:limit]:
            if not (0 <= fid < len(fields)):
                continue
            f = fields[fid]
            if not f:
                continue
            hits.append(
                {"model": f["m"], "table": f["t"], "field": f["f"], "fieldType": f["y"], "score": score}
            )
        return {"hits": hits, "totalMatches": total_matches}

    def get_model_records(self, model: str) -> Optional[List[Dict]]:
        """Lazy-load one model's full records (memoized). ``None`` when unavailable."""
        if self._dir is None or self._manifest is None:
            return None
        cached = self._model_cache.get(model)
        if cached is not None:
            return cached
        entry = next((m for m in self._manifest["models"] if m["name"] == model), None)
        if entry is None:
            return None
        try:
            file_path = self._dir / entry["file"]
            if not file_path.exists():
                return None
            with open(file_path, "r", encoding="utf-8") as f:
                records = json.load(f)
            self._model_cache[model] = records
            return records
        except Exception as err:
            sys.stderr.write(f"FieldCatalog model load error for {model}: {err}\n")
            return None

    def get_field_detail(
        self, model: str, field: str, table: Optional[str] = None
    ) -> Optional[List[Dict]]:
        """Full record lookup for a drill-down. Field match is case-insensitive."""
        records = self.get_model_records(model)
        if records is None:
            return None
        field_lower = field.lower()
        table_lower = table.lower() if table is not None else None
        return [
            r
            for r in records
            if r["field"].lower() == field_lower
            and (table_lower is None or r["table"].lower() == table_lower)
        ]


def assert_model_map_invariant(
    known_canonical_names: Set[str], catalog: Optional["Catalog"] = None
) -> None:
    """Fail-loud MODEL_MAP invariant (tightening 25h / 25b).

    The pre-built catalog (manifest + models + search index) is keyed by CANONICAL model
    names (e.g. ``Ent-Reporting-Sales``, ``OEE Monthly Reports``), which the build
    script's MODEL_MAP already resolved from the raw short names (``Z_Sales``, ``Ecomm``
    …). The equivalent drift guard for the runtime port is: every model present in the
    catalog manifest MUST be a known routing canonical name. If a catalog model has no
    corresponding routing entry, raise rather than silently drop or guess — this is what
    keeps the catalog and routing table from drifting apart.

    ``known_canonical_names`` is the set of routing ``canonicalName`` values (pass all of
    them, LOW included, since LOW entries like CMMS/OEE do appear in the catalog).
    """
    cat = catalog if catalog is not None else Catalog()
    manifest = cat.get_manifest()
    if manifest is None:
        return  # degraded mode: nothing to assert against
    unmapped = [m["name"] for m in manifest["models"] if m["name"] not in known_canonical_names]
    if unmapped:
        raise ValueError(
            "MODEL_MAP invariant violated — catalog model(s) with no matching routing "
            f"canonicalName: {', '.join(sorted(unmapped))}. "
            "Add a routing entry (or fix the name) rather than guessing."
        )


_DEFAULT_CATALOG: Optional[Catalog] = None


def default_catalog() -> Catalog:
    """Return a cached ``Catalog`` loaded from the default ``data/plugin/catalog`` dir."""
    global _DEFAULT_CATALOG
    if _DEFAULT_CATALOG is None:
        _DEFAULT_CATALOG = Catalog()
    return _DEFAULT_CATALOG
