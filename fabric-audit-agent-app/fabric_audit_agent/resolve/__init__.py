"""Newell resolution layer (Python port of the KQL MCP plugin's resolver services).

Self-contained package that resolves informal Newell terminology and field/measure
names to the authoritative canonical dataset names and EventText search patterns used
by the fabric-audit-agent, and deterministically assembles provenance-tracked
``PowerBIDatasetsWorkspace`` usage queries.

Ported (Phase 3 of the tightening master plan) from the plugin TypeScript services:
``text-normalize.ts``, ``routing-table.ts``, ``field-aliases.ts``, ``schema-link.ts``,
``catalog.ts``, ``term-resolver.ts``, ``field-resolver.ts``, ``usage-query-builder.ts``,
``artifact-lookup.ts``. Inputs live under ``fabric_audit_agent/data/plugin/``.

Design notes for the port:
  * ``normalize_for_matching`` is the ONE shared normalization used by BOTH the term
    resolver and the field resolver — never re-implemented, so they cannot drift.
  * Result payloads mirror the plugin's tool-output JSON shapes and therefore keep
    **camelCase** dict keys (``canonicalName``, ``pbiWorkspaceName``, ``daxPattern`` …),
    matching this repo's stated port-fidelity convention. Python identifiers stay
    snake_case.
  * ``AuthoritativeFilter`` is a branded wrapper class: the usage builder rejects a
    plain ``str`` and accepts only a resolver-minted ``AuthoritativeFilter`` — the
    runtime analog of the plugin's compile-time branded type.
  * Every file-loading component degrades (never crashes) on a missing/corrupt input.
"""
from __future__ import annotations

from pathlib import Path

# The extracted plugin data lives at fabric_audit_agent/data/plugin/.
# resolve/ is fabric_audit_agent/resolve/, so parents[1] is fabric_audit_agent/.
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    """Absolute path to the resolver's input data directory (``data/plugin``)."""
    return _PACKAGE_ROOT / "data" / "plugin"


def catalog_dir() -> Path:
    """Absolute path to the pre-built field catalog directory (``data/plugin/catalog``)."""
    return data_dir() / "catalog"


def newell_schema_path() -> Path:
    """Absolute path to ``newell-schema.json``."""
    return data_dir() / "newell-schema.json"


def artifacts_xlsx_path() -> Path:
    """Absolute path to ``ArtifactsMappedtoWorkspace.xlsx``."""
    return data_dir() / "ArtifactsMappedtoWorkspace.xlsx"


from .text_normalize import normalize_for_matching  # noqa: E402
from .routing_table import (  # noqa: E402
    ROUTING_TABLE,
    TABLE_VERSION,
    LAST_REVIEWED,
    match_index,
    all_canonical_names,
    known_models,
)
from .field_aliases import ALIAS_MAP, expand_field_alias_variants, strip_trailing_s  # noqa: E402
from .usage_query_builder import (  # noqa: E402
    AuthoritativeFilter,
    mint_authoritative_filter,
    SAFE_USAGE_COLUMNS,
    WORKSPACE_RETENTION_DAYS,
    build_usage_query,
    build_workspace_usage_query,
    format_provenance,
)
from .term_resolver import resolve_term  # noqa: E402
from .schema_link import SchemaLinkIndex  # noqa: E402
from .catalog import Catalog, default_catalog  # noqa: E402
from .field_resolver import FieldResolver, default_field_resolver  # noqa: E402
from .artifact_lookup import ArtifactLookup, default_artifact_lookup  # noqa: E402

__all__ = [
    "data_dir",
    "catalog_dir",
    "newell_schema_path",
    "artifacts_xlsx_path",
    "normalize_for_matching",
    "ROUTING_TABLE",
    "TABLE_VERSION",
    "LAST_REVIEWED",
    "match_index",
    "all_canonical_names",
    "known_models",
    "ALIAS_MAP",
    "expand_field_alias_variants",
    "strip_trailing_s",
    "AuthoritativeFilter",
    "mint_authoritative_filter",
    "SAFE_USAGE_COLUMNS",
    "WORKSPACE_RETENTION_DAYS",
    "build_usage_query",
    "build_workspace_usage_query",
    "format_provenance",
    "resolve_term",
    "SchemaLinkIndex",
    "Catalog",
    "default_catalog",
    "FieldResolver",
    "default_field_resolver",
    "ArtifactLookup",
    "default_artifact_lookup",
]
