"""Tests for resolve.field_resolver — 4 passes, disambiguation, usage seam."""
import pytest

from fabric_audit_agent.resolve import default_field_resolver
from fabric_audit_agent.resolve.field_resolver import FieldResolver
from fabric_audit_agent.resolve.usage_query_builder import AuthoritativeFilter


@pytest.fixture(scope="module")
def fr():
    return default_field_resolver()


def test_schema_loads(fr):
    assert fr.is_available()


def test_pass1_exact_or_ambiguous_resolved(fr):
    # "Invoice Quantity" exists in many models -> ambiguous with a combined filter.
    r = fr.resolve_field("Invoice Quantity")
    assert r["status"] in {"resolved", "ambiguous"}


def test_ambiguous_returns_authoritative_combined_filter(fr):
    r = fr.resolve_field("Invoice Quantity")
    assert r["status"] == "ambiguous"
    assert isinstance(r["combinedKqlFilter"], AuthoritativeFilter)
    # Combined filter is usable by the builder (branded) and non-empty.
    assert r["combinedKqlFilter"].fragment.strip() != ""
    # Human-readable candidate list is capped at 10, filter covers the full set.
    assert len(r["candidates"]) <= 10


def test_model_hint_narrows_to_high_confidence(fr):
    r = fr.resolve_field("Invoice Quantity", model_hint="Ent-Reporting-Sales")
    # Either narrows to a single Sales field (HIGH), or is still ambiguous within Sales.
    if r["status"] == "resolved":
        assert r["confidence"] == "HIGH"
        assert r["match"]["model"] == "Ent-Reporting-Sales"
    else:
        assert r["status"] == "ambiguous"
        assert all(c["model"] == "Ent-Reporting-Sales" for c in r["candidates"])


def test_alias_pass_medium_confidence(fr):
    # "qty" has no exact field; alias -> "quantity" should reach quantity fields.
    r = fr.resolve_field("qty")
    assert r["status"] in {"resolved", "ambiguous"}
    if r["status"] == "resolved":
        assert r["confidence"] == "MEDIUM"


def test_no_match(fr):
    r = fr.resolve_field("zzz not a real field name qqq")
    assert r["status"] == "no_match"


def test_empty_field_name(fr):
    assert fr.resolve_field("   ")["status"] == "no_match"


def test_unavailable_when_schema_missing(tmp_path):
    bad = FieldResolver(schema_path=tmp_path / "nope.json")
    assert bad.is_available() is False
    r = bad.resolve_field("Invoice Quantity")
    assert r["status"] == "unavailable"
    assert "unavailable" in r["message"].lower()


def test_field_match_shape(fr):
    r = fr.resolve_field("Invoice Quantity", model_hint="Ent-Reporting-Sales")
    match = r["match"] if r["status"] == "resolved" else r["candidates"][0]
    for key in ("model", "table", "fieldName", "fieldType", "daxPattern", "mdxPattern", "kqlFilter"):
        assert key in match
    assert match["kqlFilter"].startswith("EventText contains ")


# ── resolve_field_usage seam ──────────────────────────────────────────────────────
def test_usage_query_ready(fr):
    r = fr.resolve_field_usage("Invoice Quantity", group_by=["ExecutingUser"], timespan="30d")
    assert r["status"] == "query_ready"
    assert "PowerBIDatasetsWorkspace" in r["query"]
    # Provenance completeness: joined clauses reconstruct the query (the resolver clause
    # for an ambiguous field spans multiple lines, so join rather than count lines).
    assert "\n".join(p.clause for p in r["provenance"]) == r["query"]
    # Resolver clause embedded verbatim.
    assert any(p.origin == "resolver" for p in r["provenance"])


def test_usage_invalid_request_when_builder_rejects(fr):
    # Field resolves, but a bad groupBy column -> invalid_request (26s), not no_match.
    r = fr.resolve_field_usage("Invoice Quantity", group_by=["DatasetName"], timespan="30d")
    assert r["status"] == "invalid_request"
    assert "resolution" in r


def test_usage_no_match_never_fabricates_query(fr):
    r = fr.resolve_field_usage("zzz not real qqq", group_by=["ExecutingUser"], timespan="30d")
    assert r["status"] == "no_match"
    assert "query" not in r
