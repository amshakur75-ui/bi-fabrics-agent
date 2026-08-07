"""Tests for resolve.usage_query_builder — branding, provenance, escaping, retention."""
import pytest

from fabric_audit_agent.resolve.usage_query_builder import (
    AuthoritativeFilter,
    EqualityFilter,
    SAFE_USAGE_COLUMNS,
    WORKSPACE_RETENTION_DAYS,
    build_usage_query,
    build_workspace_usage_query,
    format_provenance,
    mint_authoritative_filter,
)

_VALID_ORIGINS = {"resolver", "artifact-inventory", "user-value", "builder-constant", "builder-derived"}


def _minted():
    return mint_authoritative_filter('EventText contains "\'Invoice Sales\'[Invoice Quantity]"')


# ── Branded-type guarantee ──────────────────────────────────────────────────────
def test_builder_rejects_plain_string():
    r = build_usage_query("EventText contains \"x\"", ["ExecutingUser"], "30d")
    assert r.ok is False
    assert "AuthoritativeFilter" in r.reason


def test_authoritative_filter_cannot_be_constructed_directly():
    with pytest.raises(TypeError):
        AuthoritativeFilter("EventText contains \"x\"")


def test_mint_then_build_succeeds():
    r = build_usage_query(_minted(), ["ExecutingUser"], "30d")
    assert r.ok is True
    assert r.query is not None


def test_empty_minted_fragment_rejected():
    r = build_usage_query(mint_authoritative_filter("   "), ["ExecutingUser"], "30d")
    assert r.ok is False
    assert "empty" in r.reason


# ── Safe column enum ──────────────────────────────────────────────────────────
def test_safe_columns_no_datasetname_uses_artifactname():
    # 25d: DatasetName removed; ArtifactName is the correct column.
    assert "DatasetName" not in SAFE_USAGE_COLUMNS
    assert "ArtifactName" in SAFE_USAGE_COLUMNS
    assert set(SAFE_USAGE_COLUMNS) == {
        "ExecutingUser", "ArtifactName", "PowerBIWorkspaceName",
        "PowerBIWorkspaceId", "OperationName", "ApplicationName",
    }


def test_unknown_groupby_column_rejected():
    r = build_usage_query(_minted(), ["DatasetName"], "30d")
    assert r.ok is False
    assert "not an allowed" in r.reason


def test_empty_groupby_rejected():
    assert build_usage_query(_minted(), [], "30d").ok is False


def test_invalid_timespan_rejected():
    assert build_usage_query(_minted(), ["ExecutingUser"], "30").ok is False
    assert build_usage_query(_minted(), ["ExecutingUser"], "1w").ok is False


def test_non_positive_topn_rejected():
    assert build_usage_query(_minted(), ["ExecutingUser"], "30d", top_n=0).ok is False
    assert build_usage_query(_minted(), ["ExecutingUser"], "30d", top_n=-1).ok is False
    # bool must not sneak through as an int.
    assert build_usage_query(_minted(), ["ExecutingUser"], "30d", top_n=True).ok is False


# ── Provenance completeness ──────────────────────────────────────────────────────
def test_provenance_covers_every_line():
    r = build_usage_query(_minted(), ["ExecutingUser", "ArtifactName"], "30d", title="my q")
    assert r.ok
    # Completeness invariant: the provenance clauses, joined, reconstruct the query
    # exactly — every emitted clause is traced, nothing untracked. (A clause may itself
    # span multiple lines, e.g. a multi-pattern resolver filter, so join — do not count
    # split lines.)
    assert "\n".join(p.clause for p in r.provenance) == r.query
    for prov in r.provenance:
        assert prov.origin in _VALID_ORIGINS
        assert prov.note  # non-empty audit note
    # The resolver clause is present and parenthesized.
    assert any(p.origin == "resolver" and p.clause.startswith("| where (") for p in r.provenance)


def test_equality_filter_origin_enforced():
    ok_f = EqualityFilter(column="ExecutingUser", value="a@b.com", origin="user-value")
    assert build_usage_query(_minted(), ["ArtifactName"], "30d", equality_filters=[ok_f]).ok is True
    bad = EqualityFilter(column="ExecutingUser", value="a@b.com", origin="resolver")
    r = build_usage_query(_minted(), ["ArtifactName"], "30d", equality_filters=[bad])
    assert r.ok is False


def test_kql_escaping_backslash_before_quote():
    # 25d: escape \\ first, then ". A value with both must escape correctly.
    f = EqualityFilter(column="ArtifactName", value='a\\b"c', origin="user-value")
    r = build_usage_query(_minted(), ["ExecutingUser"], "30d", equality_filters=[f])
    assert r.ok
    # backslash doubled, quote escaped: a\\b\"c
    assert '| where ArtifactName == "a\\\\b\\"c"' in r.query


def test_title_newline_stripped():
    r = build_usage_query(_minted(), ["ExecutingUser"], "30d", title="line1\nDROP something")
    assert r.ok
    title_line = r.query.split("\n")[0]
    assert title_line == "// line1 DROP something"
    assert "\n" not in title_line


def test_retention_warns_never_clamps():
    r = build_usage_query(_minted(), ["ExecutingUser"], "90d")
    assert r.ok
    assert r.retention_warning is not None
    assert f"{WORKSPACE_RETENTION_DAYS}d" in r.retention_warning
    # timespan preserved (never clamped).
    assert "ago(90d)" in r.query
    assert any(p.clause.startswith("// NOTE:") for p in r.provenance)


def test_no_retention_warning_within_window():
    r = build_usage_query(_minted(), ["ExecutingUser"], "30d")
    assert r.ok and r.retention_warning is None


# ── Workspace usage builder ──────────────────────────────────────────────────────
def _scope():
    return EqualityFilter(column="PowerBIWorkspaceName", value="Enterprise Sales", origin="user-value")


def test_single_window_includes_distinct_users_and_lastused():
    r = build_workspace_usage_query(_scope(), "30d")
    assert r.ok
    assert "DistinctUsers = dcount(ExecutingUser)" in r.query
    assert "LastUsed = max(TimeGenerated)" in r.query


def test_compare_periods_doubles_window_and_drops_lastused():
    r = build_workspace_usage_query(_scope(), "30d", compare_periods=True)
    assert r.ok
    assert "ago(60d)" in r.query  # doubled scan window
    assert 'Period = iff(TimeGenerated > ago(30d)' in r.query
    assert "DistinctUsers = dcount(ExecutingUser)" in r.query
    assert "LastUsed" not in r.query  # not in compare mode


def test_compare_periods_retention_gate_on_doubled_span():
    # 31d vs 31d scans 62d > 60d retention -> warns.
    r = build_workspace_usage_query(_scope(), "31d", compare_periods=True)
    assert r.ok
    assert r.retention_warning is not None


def test_scope_column_must_be_artifact_or_workspace():
    bad = EqualityFilter(column="ExecutingUser", value="x", origin="user-value")
    assert build_workspace_usage_query(bad, "30d").ok is False


# ── format_provenance ──────────────────────────────────────────────────────────
def test_format_provenance_output():
    r = build_usage_query(_minted(), ["ExecutingUser"], "30d")
    out = format_provenance(r.provenance)
    assert out.startswith("Provenance (every clause traced to an authoritative origin):")
    assert "[resolver]" in out
    assert "[builder-constant]" in out
    assert "↳" in out
