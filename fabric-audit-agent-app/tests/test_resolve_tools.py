"""Phase 3.8 — the seven Newell resolution tools registered in fabric_audit_agent.tools.

Verifies each tool is registered with a valid JSON-schema, each handler returns a
JSON-SERIALIZABLE camelCase dict (load-bearing for the MCP server), and the tool descriptions
carry the self-contained guidance (Part 23/24e): the never-hand-author-EventText rule, the
xmSQL never-search rule, and the identity-display rule."""
import json

import pytest

from fabric_audit_agent.tools import create_tool_definitions

_NEW_TOOLS = {
    "resolve_term", "resolve_field", "field_usage_query", "workspace_usage_query",
    "field_search", "field_detail", "artifact_lookup",
}


@pytest.fixture(scope="module")
def defs():
    return {d["name"]: d for d in create_tool_definitions()}


def test_all_seven_registered_with_valid_schema(defs):
    for name in _NEW_TOOLS:
        assert name in defs, f"{name} not registered"
        d = defs[name]
        assert callable(d["handler"])
        schema = d["input_schema"]
        assert schema["type"] == "object"
        assert isinstance(schema["properties"], dict)
        assert isinstance(schema["required"], list)
        # required entries must be declared properties (load-bearing for the MCP server)
        for req in schema["required"]:
            assert req in schema["properties"], f"{name}: required '{req}' not in properties"


def _assert_json(result):
    json.dumps(result)  # must not raise
    assert isinstance(result, dict)
    return result


def test_resolve_term_resolves_informal_name(defs):
    r = _assert_json(defs["resolve_term"]["handler"]({"term": "Sales"}))
    assert r["status"] == "resolved"
    assert r["canonicalName"] == "Ent-Reporting-Sales"


def test_resolve_field_ambiguous_filter_is_serializable(defs):
    # 'Invoice Quantity' is ambiguous — the branded AuthoritativeFilter in combinedKqlFilter must
    # be stringified so the result stays JSON-serializable.
    r = _assert_json(defs["resolve_field"]["handler"]({"field": "Invoice Quantity"}))
    assert r["status"] in ("resolved", "ambiguous", "no_match")
    if r["status"] == "ambiguous":
        assert isinstance(r["combinedKqlFilter"], str)


def test_field_usage_query_builds_provenanced_query(defs):
    r = _assert_json(defs["field_usage_query"]["handler"]({"field": "Invoice Quantity"}))
    assert r["status"] == "query_ready"
    assert "PowerBIDatasetsWorkspace" in r["query"]
    assert isinstance(r["provenance"], list) and r["provenance"]           # serialized dicts
    assert all(isinstance(p, dict) for p in r["provenance"])
    assert "provenanceText" in r


def test_workspace_usage_query_builds(defs):
    r = _assert_json(defs["workspace_usage_query"]["handler"](
        {"scopeColumn": "PowerBIWorkspaceName", "scopeValue": "Ent-Reporting-Sales"}))
    assert r["status"] == "query_ready"
    assert "Ent-Reporting-Sales" in r["query"]


def test_workspace_usage_query_rejects_bad_column(defs):
    r = _assert_json(defs["workspace_usage_query"]["handler"](
        {"scopeColumn": "NotAColumn", "scopeValue": "x"}))
    assert r["status"] == "invalid_request"


def test_field_search_returns_hits(defs):
    r = _assert_json(defs["field_search"]["handler"]({"query": "invoice"}))
    assert r["status"] == "ok"
    assert isinstance(r["hits"], list) and r["totalMatches"] >= len(r["hits"])


def test_field_detail_found_and_invalid_request(defs):
    r = _assert_json(defs["field_detail"]["handler"](
        {"model": "Ent-Reporting-Sales", "field": "Invoice Quantity"}))
    assert r["status"] in ("found", "not_found")
    bad = _assert_json(defs["field_detail"]["handler"]({"model": "", "field": ""}))
    assert bad["status"] == "invalid_request"


def test_artifact_lookup_requires_exactly_one_param(defs):
    r = _assert_json(defs["artifact_lookup"]["handler"]({"artifactName": "Ent-Reporting-Sales"}))
    assert r["status"] in ("found", "multiple", "not_found", "unavailable")
    both = _assert_json(defs["artifact_lookup"]["handler"](
        {"artifactName": "x", "artifactId": "y"}))
    assert both["status"] == "invalid_request"


def test_descriptions_carry_self_contained_guidance(defs):
    # Part 23: the never-hand-author-EventText rule travels with the tool schema itself.
    assert "never see, write, edit, or verify the EventText filter" in defs["field_usage_query"]["description"]
    # 24e: xmSQL never-search rule on the field-resolution tools.
    assert "xmSQL" in defs["resolve_field"]["description"]
    assert "xmSQL" in defs["field_usage_query"]["description"]
    # identity-display rule surfaced where results include ExecutingUser.
    assert "newellco.com" in defs["field_usage_query"]["description"]
    # resolve-term-first sequencing guidance.
    assert "FIRST" in defs["resolve_term"]["description"]
