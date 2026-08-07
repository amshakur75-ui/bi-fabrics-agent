"""Tests for resolve.catalog — lazy loading, search scoring, degraded mode, MODEL_MAP."""
import pytest

from fabric_audit_agent.resolve.catalog import (
    Catalog,
    assert_model_map_invariant,
    default_catalog,
    tokenize,
)
from fabric_audit_agent.resolve.routing_table import ROUTING_TABLE


def test_tokenize_splits_on_ws_underscore_hyphen():
    assert tokenize("Invoice_Quantity-Change name") == ["invoice", "quantity", "change", "name"]
    assert tokenize("") == []


def test_default_catalog_available():
    cat = default_catalog()
    assert cat.is_available()
    manifest = cat.get_manifest()
    assert manifest["totalRecords"] == 20683
    assert len(manifest["models"]) == 14


def test_token_index_shape():
    cat = default_catalog()
    idx = cat.get_field_name_token_index()
    assert idx is not None
    assert "quantity" in idx
    assert isinstance(idx["quantity"], set)
    assert any("Quantity" in name for name in idx["quantity"])


def test_search_fields_scoring_prefers_and_matches():
    cat = default_catalog()
    res = cat.search_fields("invoice quantity", limit=5)
    assert res is not None
    assert res["totalMatches"] > 0
    # An exact "Invoice Quantity" field scores 2 (both tokens) and ranks at the top.
    top = res["hits"][0]
    assert top["score"] == 2
    assert len(res["hits"]) <= 5


def test_search_fields_model_filter():
    cat = default_catalog()
    res = cat.search_fields("quantity", model="Ent-Reporting-Sales", limit=10)
    assert res is not None
    assert all(h["model"] == "Ent-Reporting-Sales" for h in res["hits"])


def test_search_fields_limit_capped_at_25():
    cat = default_catalog()
    res = cat.search_fields("date", limit=100)
    assert res is not None
    assert len(res["hits"]) <= 25


def test_lazy_model_records_and_field_detail():
    cat = default_catalog()
    recs = cat.get_model_records("Ent-Reporting-DTC")
    assert recs is not None and len(recs) == 400
    detail = cat.get_field_detail("Ent-Reporting-DTC", recs[0]["field"])
    assert detail and detail[0]["field"] == recs[0]["field"]


def test_unknown_model_records_returns_none():
    cat = default_catalog()
    assert cat.get_model_records("No-Such-Model") is None


def test_degraded_mode_on_bad_dir(tmp_path):
    cat = Catalog(directory=tmp_path)  # empty dir -> load fails
    assert cat.is_available() is False
    assert cat.load_error is not None
    assert cat.get_manifest() is None
    assert cat.get_field_name_token_index() is None
    assert cat.search_fields("anything") is None
    assert cat.get_model_records("Ent-Reporting-DTC") is None


# ── MODEL_MAP fail-loud invariant (25h) ──────────────────────────────────────────
def test_model_map_invariant_passes_with_routing_names():
    names = {e["canonicalName"] for e in ROUTING_TABLE}
    assert_model_map_invariant(names, catalog=default_catalog())  # no raise


def test_model_map_invariant_raises_on_drift():
    incomplete = {e["canonicalName"] for e in ROUTING_TABLE} - {"Ent-Reporting-Sales"}
    with pytest.raises(ValueError, match="MODEL_MAP invariant violated"):
        assert_model_map_invariant(incomplete, catalog=default_catalog())
