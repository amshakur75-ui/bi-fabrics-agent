from fabric_audit_agent.query.deeplinks import kusto_deeplink

_FABRIC_HOST = "https://mycluster.kusto.fabric.microsoft.com"
_ADX_HOST = "https://help.kusto.windows.net"
_MULTILINE_KQL = "CapacityEvents\n| take 5"


def test_kusto_deeplink_fabric_host_contains_encoded_query_and_database():
    url = kusto_deeplink(_FABRIC_HOST, "MyDb", _MULTILINE_KQL)
    assert url is not None
    assert url.startswith("https://dataexplorer.azure.com/clusters/")
    assert "mycluster.kusto.fabric.microsoft.com" in url
    assert "/databases/MyDb" in url
    assert "%0A" in url  # newline
    assert "%20" in url  # space
    assert "%7C" in url  # pipe


def test_kusto_deeplink_adx_host_contains_encoded_query_and_database():
    url = kusto_deeplink(_ADX_HOST, "MyDb", _MULTILINE_KQL)
    assert url is not None
    assert url.startswith("https://dataexplorer.azure.com/clusters/")
    assert "help.kusto.windows.net" in url
    assert "%0A" in url
    assert "%20" in url
    assert "%7C" in url


def test_kusto_deeplink_database_with_space_is_encoded_and_identifiable():
    url = kusto_deeplink(_ADX_HOST, "My Db Name", _MULTILINE_KQL)
    assert url is not None
    # database must be visible/identifiable -- encoded (space -> %20), not raw.
    assert "My%20Db%20Name" in url
    assert "My Db Name" not in url


def test_kusto_deeplink_unknown_host_returns_none():
    assert kusto_deeplink("https://evil.com", "db", "T | take 1") is None


def test_kusto_deeplink_non_https_returns_none():
    assert kusto_deeplink("http://mycluster.kusto.windows.net", "db", "T | take 1") is None


def test_kusto_deeplink_empty_database_returns_none():
    assert kusto_deeplink(_ADX_HOST, "", "T | take 1") is None
    assert kusto_deeplink(_ADX_HOST, None, "T | take 1") is None


def test_kusto_deeplink_empty_kql_returns_none():
    assert kusto_deeplink(_ADX_HOST, "db", "") is None
    assert kusto_deeplink(_ADX_HOST, "db", None) is None


def test_kusto_deeplink_lookalike_host_returns_none():
    # "kusto.windows.net.evil.com" contains the allowlisted string but does not truly end with
    # it -- proves this reuses the suffix-anchored allowlist, not a naive substring match.
    assert kusto_deeplink("https://kusto.windows.net.evil.com", "db", "T | take 1") is None


def test_kusto_deeplink_is_deterministic():
    url1 = kusto_deeplink(_FABRIC_HOST, "MyDb", _MULTILINE_KQL)
    url2 = kusto_deeplink(_FABRIC_HOST, "MyDb", _MULTILINE_KQL)
    assert url1 == url2
