"""Verify-in-Fabric deeplinks. Adapted from microsoft/fabric-rti-mcp's
``_build_adx_deeplink``/``_build_fabric_deeplink`` (MIT). Pure stdlib.

``kusto_deeplink`` builds a canonical ADX/Fabric web-explorer URL that reruns the EXACT KQL a
tool result was built from, so a human can click through and verify a quoted figure live. The
query is percent-encoded (``urllib.parse.quote(kql, safe="")``) directly into the URL -- NOT
gzip/base64 -- matching the plain ``?query=`` form the web explorer accepts. Deterministic, no
clock/random. Reuses the SAME anti-SSRF cluster-URI allowlist as ``query.kql_guard`` (one source
of truth for the allowlist -- never raises; returns ``None`` for anything it can't safely link).
"""
from urllib.parse import quote, urlparse

from .kql_guard import assert_kusto_host


def kusto_deeplink(cluster_uri, database, kql):
    """Build a Fabric/ADX web-explorer URL that reruns *kql* against *cluster_uri*/*database*.

    Returns ``None`` (never raises) when:
      - *cluster_uri* fails the anti-SSRF host allowlist / https check (``assert_kusto_host``).
      - *database* or *kql* is falsy/empty (nothing meaningful to link).

    URL form: ``https://dataexplorer.azure.com/clusters/<host>/databases/<database>?query=<kql>``
    -- both *database* and *kql* are percent-encoded (``safe=""``), so the database stays
    identifiable in the URL even when it contains spaces.
    """
    if not database or not kql:
        return None

    try:
        normalized = assert_kusto_host(cluster_uri)
    except ValueError:
        return None

    host = urlparse(normalized).hostname
    encoded_database = quote(database, safe="")
    encoded_kql = quote(kql, safe="")
    return f"https://dataexplorer.azure.com/clusters/{host}/databases/{encoded_database}?query={encoded_kql}"
