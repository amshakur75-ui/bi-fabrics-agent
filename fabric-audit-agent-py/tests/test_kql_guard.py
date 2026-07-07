import pytest

from fabric_audit_agent.query.kql_guard import (
    assert_kusto_host,
    assert_read_only_kql,
    escape_entity,
    escape_string,
    first_statement,
)


def test_escape_string_neutralizes_quote_breakout():
    assert escape_string('a"; T | take 999 //') == 'a\\"; T | take 999 //'
    assert escape_string("back\\slash") == "back\\\\slash"
    assert escape_string("nul\x00byte") == "nulbyte"


def test_escape_entity_brackets_and_rejects_control_chars():
    assert escape_entity("My Table") == "['My Table']"
    assert escape_entity("T'able") == "['T\\'able']"
    with pytest.raises(ValueError):
        escape_entity("bad\nname")


def test_first_statement_cuts_stacked_statements():
    assert first_statement("T | take 5; T2 | take 9") == "T | take 5"
    assert first_statement('T | where x == "a;b" | take 5') == 'T | where x == "a;b" | take 5'
    # escaped-backslash at the string boundary must NOT keep us "in string" forever:
    assert first_statement('T | where x == "a\\\\"; T2 | take 9') == 'T | where x == "a\\\\"'
    assert first_statement("T | take 5") == "T | take 5"


def test_assert_read_only_kql_passes_plain_query():
    kql = 'T | where x == "a" | take 10'
    assert assert_read_only_kql(kql) == kql


def test_assert_read_only_kql_rejects_control_command_after_pipe():
    with pytest.raises(ValueError):
        assert_read_only_kql("T | take 1 | .drop table X")


def test_assert_read_only_kql_rejects_control_command_after_semicolon():
    with pytest.raises(ValueError):
        assert_read_only_kql("T | take 1; .drop table X")


def test_assert_read_only_kql_rejects_control_command_at_start():
    with pytest.raises(ValueError):
        assert_read_only_kql(".drop table X")


def test_assert_read_only_kql_rejects_boolean_tautology():
    with pytest.raises(ValueError):
        assert_read_only_kql("T | where x==1 or 1==1")


def test_assert_read_only_kql_rejects_oversized_query():
    with pytest.raises(ValueError):
        assert_read_only_kql("T" * 10001)


def test_assert_read_only_kql_allows_control_keyword_inside_string_literal():
    kql = 'T | where Message == ".drop table X" | take 5'
    assert assert_read_only_kql(kql) == kql


# ---------------------------------------------------------------------------
# assert_kusto_host -- anti-SSRF cluster-URI allowlist (Azure-MCP ValidateAndNormalizeClusterUri)
# ---------------------------------------------------------------------------

def test_assert_kusto_host_accepts_allowlisted_https_host():
    assert assert_kusto_host("https://mycluster.kusto.windows.net") == "https://mycluster.kusto.windows.net"


def test_assert_kusto_host_strips_trailing_slash():
    assert assert_kusto_host("https://mycluster.kusto.windows.net/") == "https://mycluster.kusto.windows.net"


def test_assert_kusto_host_accepts_all_allowlisted_suffixes():
    for uri in (
        "https://c.kusto.windows.net",
        "https://c.kusto.fabric.microsoft.com",
        "https://c.adx.monitor.azure.com",
        "https://c.kusto.usgovcloudapi.net",
        "https://c.kusto.chinacloudapi.cn",
    ):
        assert assert_kusto_host(uri) == uri


def test_assert_kusto_host_rejects_non_https_scheme():
    with pytest.raises(ValueError):
        assert_kusto_host("http://mycluster.kusto.windows.net")


def test_assert_kusto_host_rejects_non_allowlisted_host():
    with pytest.raises(ValueError):
        assert_kusto_host("https://evil.example.com")


def test_assert_kusto_host_rejects_lookalike_host_not_true_suffix():
    # "kusto.windows.net.evil.com" contains the allowlisted string but does NOT end with it.
    with pytest.raises(ValueError):
        assert_kusto_host("https://mycluster.kusto.windows.net.evil.com")
