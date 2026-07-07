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
