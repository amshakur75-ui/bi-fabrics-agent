"""Lakebase alert-chat writer — SQL/row shape via an injected fake connection (no live DB)."""
import json

import pytest

from fabric_audit_agent.adapters.chat_store_lakebase import (
    create_alert_chat,
    _endpoint_path,
    _lakebase_conn,
)


class _FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))


class _FakeConn:
    def __init__(self):
        self.cur = _FakeCursor()
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_inserts_public_system_chat_and_assistant_message():
    conn = _FakeConn()
    md = "## Throttle\nSustained during the 09:00 refresh."
    cid = create_alert_chat(md, "Throttling on capacity", conn=conn)

    assert conn.committed is True
    assert len(conn.cur.calls) == 2
    (sql1, p1), (sql2, p2) = conn.cur.calls

    # Chat: public, system-owned, id returned
    assert '"Chat"' in sql1
    assert p1[0] == cid and p1[2] == "Throttling on capacity"
    assert p1[3] == "fabric-audit-agent" and p1[4] == "public"

    # Message: assistant, parts=[{text: markdown}], attachments=[]
    assert '"Message"' in sql2 and p2[1] == cid and p2[2] == "assistant"
    assert json.loads(p2[3]) == [{"type": "text", "text": md}]
    assert p2[4] == "[]"

    # injected conn is not closed by the writer (caller owns it)
    assert conn.closed is False


def test_endpoint_path_default_from_instance(monkeypatch):
    for k in ("FABRIC_LAKEBASE_ENDPOINT_PATH", "FABRIC_LAKEBASE_BRANCH",
              "FABRIC_LAKEBASE_ENDPOINT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FABRIC_LAKEBASE_INSTANCE", "fabrics-audit-agent-memory")
    assert _endpoint_path() == (
        "projects/fabrics-audit-agent-memory/branches/production/endpoints/primary")


def test_endpoint_path_full_override_wins(monkeypatch):
    monkeypatch.setenv("FABRIC_LAKEBASE_INSTANCE", "ignored")
    monkeypatch.setenv("FABRIC_LAKEBASE_ENDPOINT_PATH",
                       "projects/x/branches/dev/endpoints/ro")
    assert _endpoint_path() == "projects/x/branches/dev/endpoints/ro"


def test_lakebase_conn_uses_autoscaling_postgres_api(monkeypatch):
    """Regression: Lakebase here is an Autoscaling PROJECT, so the credential must come from
    ``w.postgres.generate_database_credential(<endpoint path>)`` — NOT the Provisioned
    ``w.database.generate_database_credential(instance_names=[...])`` (which 404s 'instance not
    found' and silently broke every alert deep-link)."""
    monkeypatch.setenv("FABRIC_LAKEBASE_HOST", "the-host")
    monkeypatch.setenv("FABRIC_LAKEBASE_USER", "the-user")
    monkeypatch.setenv("FABRIC_LAKEBASE_INSTANCE", "inst")
    monkeypatch.delenv("FABRIC_LAKEBASE_ENDPOINT_PATH", raising=False)
    monkeypatch.delenv("FABRIC_LAKEBASE_BRANCH", raising=False)
    monkeypatch.delenv("FABRIC_LAKEBASE_ENDPOINT_ID", raising=False)
    seen = {}

    class _Postgres:
        def generate_database_credential(self, endpoint):
            seen["endpoint"] = endpoint
            return type("Cred", (), {"token": "short-lived-token"})()

    class _Database:
        def generate_database_credential(self, *a, **k):  # must NOT be used
            raise AssertionError("used the Provisioned database API — wrong for an Autoscaling project")

    class _W:
        postgres = _Postgres()
        database = _Database()

    def _fake_connect(**kw):
        seen["connect"] = kw
        return "CONN"

    out = _lakebase_conn(client=_W(), connect=_fake_connect)
    assert out == "CONN"
    assert seen["endpoint"] == "projects/inst/branches/production/endpoints/primary"
    assert seen["connect"]["host"] == "the-host"
    assert seen["connect"]["user"] == "the-user"
    assert seen["connect"]["password"] == "short-lived-token"
    assert seen["connect"]["sslmode"] == "require"
