"""Kusto/LA client hardening tests — read-only-hardline CRP shape + LA Prefer-wait header.

Offline only: never hits a live endpoint, never imports azure-kusto-data. Fakes capture the
``properties`` / ``headers`` args the builders pass through, so we can assert on shape without
the real SDK installed.
"""

import json

from fabric_audit_agent.adapters.clients import build_kusto_query, EntraHttp


def _props_json(props):
    """Stringify captured CRP-shaped properties as JSON, matching the real SDK's
    ``ClientRequestProperties.to_json()`` shape closely enough for substring assertions —
    whether ``props`` is a real CRP (has ``.to_json()``) or the plain dict the fake path builds."""
    if hasattr(props, "to_json"):
        return props.to_json()
    return json.dumps(props)


# ---------- build_kusto_query ----------
class _FakeTable:
    columns = []
    rows = []


class _FakeResp:
    primary_results = [_FakeTable()]


class _FakeKusto:
    def __init__(self):
        self.calls = []

    def execute(self, db, kql, properties=None):
        self.calls.append((db, kql, properties))
        return _FakeResp()


def test_kusto_sets_readonly_hardline_timeout_and_request_id():
    fake = _FakeKusto()
    build_kusto_query("https://c", "db", "t", "cid", "sec", client=fake)("T | take 1")
    db, kql, props = fake.calls[0]
    assert db == "db" and kql == "T | take 1"
    s = _props_json(props).lower()
    # Distinct assertions: the plain flag and the hardline flag are separate keys — a missing
    # plain "request_readonly" must be detectable even if hardline is present.
    assert '"request_readonly":' in s
    assert '"request_readonly_hardline":' in s
    assert "true" in s
    assert "FAA.query:" in _props_json(props)


def test_kusto_default_action_is_query_and_custom_action_flows_through():
    fake = _FakeKusto()
    build_kusto_query("https://c", "db", "t", "cid", "sec", client=fake, action="diag")("T")
    _, _, props = fake.calls[0]
    assert "FAA.diag:" in _props_json(props)


def test_kusto_query_shapes_rows_into_dicts():
    class _Col:
        def __init__(self, name):
            self.column_name = name

    class _Table:
        columns = [_Col("A"), _Col("B")]
        rows = [[1, 2], [3, 4]]

    class _Resp:
        primary_results = [_Table()]

    class _Kusto:
        def execute(self, db, kql, properties=None):
            return _Resp()

    rows = build_kusto_query("https://c", "db", "t", "cid", "sec", client=_Kusto())("T")
    assert rows == [{"A": 1, "B": 2}, {"A": 3, "B": 4}]


# ---------- EntraHttp.post_json headers passthrough ----------
class _FakeJsonResp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True}


class _FakeSession:
    def __init__(self):
        self.post_args = None

    def post(self, url, json=None, headers=None, timeout=None):
        self.post_args = {"url": url, "json": json, "headers": headers, "timeout": timeout}
        return _FakeJsonResp()


def test_entra_http_post_json_forwards_custom_headers():
    sess = _FakeSession()
    http = EntraHttp(lambda: "TOKEN", session=sess)
    http.post_json("https://api", {"a": 1}, headers={"Prefer": "wait=240"})
    assert sess.post_args["headers"]["Prefer"] == "wait=240"
    # Existing auth/accept behavior must survive the merge.
    assert sess.post_args["headers"]["Authorization"] == "Bearer TOKEN"


def test_entra_http_post_json_without_headers_keeps_default_behavior():
    sess = _FakeSession()
    http = EntraHttp(lambda: "TOKEN", session=sess)
    http.post_json("https://api", {"a": 1})
    assert sess.post_args["headers"] == {"Authorization": "Bearer TOKEN", "Accept": "application/json"}


def test_build_log_analytics_query_sends_prefer_wait_header(monkeypatch):
    # build_entra_token_provider needs msal (a prod-only, lazily-imported extra not installed
    # offline) — stub it out so this test stays offline while still exercising the real
    # build_log_analytics_query wiring for the Prefer header.
    import fabric_audit_agent.adapters.clients as clients_mod

    monkeypatch.setattr(clients_mod, "build_entra_token_provider", lambda *a, **k: (lambda: "TOKEN"))
    sess = _FakeSession()
    query = clients_mod.build_log_analytics_query(
        "wsid", "t", "cid", "sec", session=sess, timeout_seconds=120
    )
    query("T | take 1")
    assert sess.post_args["headers"]["Prefer"] == "wait=120"


def test_build_log_analytics_query_defaults_timeout_to_240(monkeypatch):
    import fabric_audit_agent.adapters.clients as clients_mod

    monkeypatch.setattr(clients_mod, "build_entra_token_provider", lambda *a, **k: (lambda: "TOKEN"))
    sess = _FakeSession()
    query = clients_mod.build_log_analytics_query("wsid", "t", "cid", "sec", session=sess)
    query("T | take 1")
    assert sess.post_args["headers"]["Prefer"] == "wait=240"


# ---------- query_with_stats: QueryCompletionInformation is a SECONDARY table ----------
# Real Kusto responses carry per-query cost stats in a "QueryCompletionInformation" table
# that is DROPPED by the primary-result path (resp.primary_results[0]). query_with_stats must
# iterate resp.tables (the full multi-table response) to find it. Row shape + Payload JSON
# shape below match the documented KustoResponseDataSet / QueryResourceConsumption format
# (Microsoft Learn "Query Resource Consumption"): Payload is a JSON *string* column, the stats
# row is marked EventTypeName == "QueryResourceConsumption", cpu time lives under
# resource_usage.cpu["total cpu"] as an "HH:MM:SS[.ffffff]" timespan string, wall time is a
# top-level ExecutionTime in SECONDS, and extents scanned is
# input_dataset_statistics.extents.scanned.

class _Col:
    def __init__(self, name):
        self.column_name = name


class _NamedTable:
    """A single table in a KustoResponseDataSet-shaped multi-table response."""

    def __init__(self, columns, rows, table_kind=None, table_name=None):
        self.columns = [_Col(c) for c in columns]
        self.rows = rows
        self.table_kind = table_kind
        self.table_name = table_name


def _completion_table(payload_obj, event_type_name="QueryResourceConsumption"):
    payload_json = json.dumps(payload_obj)
    return _NamedTable(
        ["Timestamp", "ClientRequestId", "EventTypeName", "Payload"],
        [["2026-07-06T00:00:00Z", "FAA.query:abc", event_type_name, payload_json]],
        table_name="QueryCompletionInformation",
    )


def _primary_table(columns, rows):
    return _NamedTable(columns, rows, table_name="PrimaryResult")


class _MultiTableResp:
    """Mimics KustoResponseDataSet: exposes BOTH primary_results (what query() reads) and the
    full tables list (what query_with_stats must read to reach QueryCompletionInformation)."""

    def __init__(self, tables):
        self.tables = tables
        self.primary_results = [t for t in tables if t.table_name == "PrimaryResult"]


_RESOURCE_PAYLOAD = {
    "QueryHash": "add172cd28dde0eb",
    "ExecutionTime": 1.5,  # seconds
    "resource_usage": {
        "cpu": {
            "user": "00:00:00",
            "kernel": "00:00:00",
            "total cpu": "00:00:02.5000000",
        },
    },
    "input_dataset_statistics": {
        "extents": {"total": 4, "scanned": 3},
    },
}


def test_query_with_stats_parses_cpu_and_extents_from_completion_table():
    primary = _primary_table(["A", "B"], [[1, 2], [3, 4]])
    completion = _completion_table(_RESOURCE_PAYLOAD)

    class _Kusto:
        def execute(self, db, kql, properties=None):
            return _MultiTableResp([primary, completion])

    query = build_kusto_query("https://c", "db", "t", "cid", "sec", client=_Kusto())
    rows, stats = query.query_with_stats("T")

    # rows == same shape as query() -- from the primary table.
    assert rows == [{"A": 1, "B": 2}, {"A": 3, "B": 4}]

    assert stats is not None
    assert stats["extentsScanned"] == 3
    # cpuTime is derived from the "00:00:02.5000000" timespan -> 2.5 seconds.
    assert stats["cpuTime"] == 2.5
    # executionTimeMs is ExecutionTime (seconds) * 1000.
    assert stats["executionTimeMs"] == 1500.0


def test_query_with_stats_returns_none_when_no_completion_table():
    primary = _primary_table(["A"], [[1]])

    class _Kusto:
        def execute(self, db, kql, properties=None):
            return _MultiTableResp([primary])

    query = build_kusto_query("https://c", "db", "t", "cid", "sec", client=_Kusto())
    rows, stats = query.query_with_stats("T")
    assert rows == [{"A": 1}]
    assert stats is None


def test_query_with_stats_returns_none_when_completion_table_has_no_resource_row():
    # A completion table present, but its row is some other EventTypeName (not the resource-
    # consumption one) -- must not crash, must return None.
    primary = _primary_table(["A"], [[1]])
    completion = _completion_table({"irrelevant": True}, event_type_name="QueryCompletion")

    class _Kusto:
        def execute(self, db, kql, properties=None):
            return _MultiTableResp([primary, completion])

    query = build_kusto_query("https://c", "db", "t", "cid", "sec", client=_Kusto())
    rows, stats = query.query_with_stats("T")
    assert rows == [{"A": 1}]
    assert stats is None


def test_query_with_stats_tolerates_malformed_payload_json():
    # Payload column holds garbage (not valid JSON) -- must not crash; missing fields -> None.
    primary = _primary_table(["A"], [[1]])
    completion = _NamedTable(
        ["Timestamp", "EventTypeName", "Payload"],
        [["2026-07-06T00:00:00Z", "QueryResourceConsumption", "{not valid json"]],
        table_name="QueryCompletionInformation",
    )

    class _Kusto:
        def execute(self, db, kql, properties=None):
            return _MultiTableResp([primary, completion])

    query = build_kusto_query("https://c", "db", "t", "cid", "sec", client=_Kusto())
    rows, stats = query.query_with_stats("T")
    assert rows == [{"A": 1}]
    # Tolerant: never crash. Either None entirely, or a dict with None fields.
    if stats is not None:
        assert stats.get("cpuTime") is None
        assert stats.get("executionTimeMs") is None
        assert stats.get("extentsScanned") is None


def test_query_with_stats_tolerates_missing_nested_fields():
    # Payload is valid JSON but missing the nested keys we look for -- fields come back None,
    # not a crash, and fields that ARE present still populate correctly.
    primary = _primary_table(["A"], [[1]])
    completion = _completion_table({"ExecutionTime": 0.25})  # no resource_usage, no extents

    class _Kusto:
        def execute(self, db, kql, properties=None):
            return _MultiTableResp([primary, completion])

    query = build_kusto_query("https://c", "db", "t", "cid", "sec", client=_Kusto())
    rows, stats = query.query_with_stats("T")
    assert stats is not None
    assert stats["executionTimeMs"] == 250.0
    assert stats["cpuTime"] is None
    assert stats["extentsScanned"] is None


def test_query_unaffected_by_stats_addition_still_reads_primary_only():
    # query() (not query_with_stats) must keep working unchanged, even when a
    # QueryCompletionInformation table is present in the response.
    primary = _primary_table(["A", "B"], [[1, 2]])
    completion = _completion_table(_RESOURCE_PAYLOAD)

    class _Kusto:
        def execute(self, db, kql, properties=None):
            return _MultiTableResp([primary, completion])

    query = build_kusto_query("https://c", "db", "t", "cid", "sec", client=_Kusto())
    assert query("T") == [{"A": 1, "B": 2}]


def test_query_with_stats_uses_readonly_hardline_crp_like_query():
    # query_with_stats must keep the same read-only CRP behavior as query(): the fake captures
    # (db, kql, properties) on execute() so we can assert the CRP shape is unchanged.
    class _StatsAwareFakeKusto:
        def __init__(self):
            self.calls = []

        def execute(self, db, kql, properties=None):
            self.calls.append((db, kql, properties))
            return _MultiTableResp([_primary_table([], [])])

    fake = _StatsAwareFakeKusto()
    query = build_kusto_query("https://c", "db", "t", "cid", "sec", client=fake)
    rows, stats = query.query_with_stats("T | take 1")
    assert rows == []
    assert stats is None
    db, kql, props = fake.calls[0]
    assert db == "db" and kql == "T | take 1"
    s = _props_json(props).lower()
    assert '"request_readonly":' in s
    assert '"request_readonly_hardline":' in s
    assert "FAA.query:" in _props_json(props)


def test_query_with_stats_is_accessible_as_companion_of_query():
    """query_with_stats(kql) -> (rows, stats) is a companion callable exposed alongside the
    plain query(kql) -> rows callable returned by build_kusto_query -- attached as an attribute
    so callers can do either `query(kql)` or `query.query_with_stats(kql)` from one builder call."""
    primary = _primary_table(["A"], [[1]])
    completion = _completion_table(_RESOURCE_PAYLOAD)

    class _Kusto:
        def execute(self, db, kql, properties=None):
            return _MultiTableResp([primary, completion])

    query = build_kusto_query("https://c", "db", "t", "cid", "sec", client=_Kusto())
    assert callable(query.query_with_stats)
    rows, stats = query.query_with_stats("T")
    assert rows == [{"A": 1}]
    assert stats["extentsScanned"] == 3
