"""Tests for resolve.schema_link — the 4 Pass-1c guardrails + degraded mode."""
from fabric_audit_agent.resolve.catalog import default_catalog
from fabric_audit_agent.resolve.schema_link import (
    RUNAWAY_SANITY_CEILING,
    SINGLE_TOKEN_MIN_LENGTH,
    SchemaLinkIndex,
)


def _index():
    return SchemaLinkIndex(catalog=default_catalog())


def test_index_loads_from_built_catalog():
    idx = _index()
    assert idx.is_available()


def test_guardrail_single_token_min_length():
    # Guardrail 1: a single token < 4 chars returns [] (that's ALIAS_MAP's job).
    idx = _index()
    assert SINGLE_TOKEN_MIN_LENGTH == 4
    assert idx.find_linked_field_names("qty") == []   # 3 chars
    # A >=4 char single token under the runaway ceiling hits ("invoice" -> 274 names).
    hits = idx.find_linked_field_names("invoice")
    assert len(hits) > 0
    assert any("Invoice" in h for h in hits)


def test_guardrail_runaway_ceiling_drops_overbroad_single_token():
    # Guardrail 3: "quantity" maps to >500 distinct field names -> dropped (pathological).
    idx = _index()
    assert idx.find_linked_field_names("quantity") == []


def test_guardrail_multi_token_and_intersection():
    # Guardrail 2: "invoice quantity" returns only names containing BOTH tokens.
    idx = _index()
    hits = idx.find_linked_field_names("invoice quantity")
    assert len(hits) > 0
    for name in hits:
        low = name.lower()
        assert "invoice" in low and "quantity" in low


def test_guardrail_alias_variants_tried_first():
    # Guardrail 4: an alias-expanded variant ("invoice") is tried before the raw
    # 3-char "inv" (which alone returns [] under guardrail 1).
    idx = _index()
    assert idx.find_linked_field_names("inv") == []      # raw 3-char alone: nothing
    hits = idx.find_linked_field_names("inv", ["invoice"])
    assert len(hits) > 0
    assert any("Invoice" in h for h in hits)


def test_no_match_returns_empty():
    idx = _index()
    assert idx.find_linked_field_names("zzzznotarealtoken") == []


def test_runaway_ceiling_constant():
    assert RUNAWAY_SANITY_CEILING == 500


def test_degraded_when_no_index():
    # No catalog, no legacy path -> index unavailable -> Pass 1c is a no-op.
    idx = SchemaLinkIndex(catalog=None, legacy_catalog_path=None)
    assert idx.is_available() is False
    assert idx.find_linked_field_names("quantity") == []
