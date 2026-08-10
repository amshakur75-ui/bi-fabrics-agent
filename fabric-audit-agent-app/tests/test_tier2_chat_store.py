"""Lakebase alert-chat writer — SQL/row shape via an injected fake connection (no live DB)."""
import json

import pytest

from fabric_audit_agent.adapters.chat_store_lakebase import (
    LakebaseWriteError,
    create_alert_chat,
    create_ticket_writer,
    _endpoint_path,
    _lakebase_conn,
    _resolve_pg_user,
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
    # No execution identity here -> FABRIC_LAKEBASE_USER is the local-dev fallback (16a); see
    # test_resolve_pg_user_* below for the precedence itself.
    monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)
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


# ---------------------------------------------------------------------------
# 16a: Postgres connecting-user identity resolution
# ---------------------------------------------------------------------------

_SP_CLIENT_ID = "3f7c1e02-9a4b-4d1e-8f60-2b5c7a9d1e44"   # UUID-shaped, like a real one


class _FakeMe:
    def __init__(self, user_name):
        self.user_name = user_name


class _FakeClient:
    """Minimal WorkspaceClient stand-in exposing current_user.me()."""
    def __init__(self, user_name=None, raises=False):
        self._raises = raises
        outer = self

        class _CU:
            def me(self):
                if outer._raises:
                    raise RuntimeError("workspace unreachable")
                return _FakeMe(user_name)
        self.current_user = _CU()


def test_resolve_pg_user_prefers_execution_identity_over_hardcoded_override():
    """The App's own service-principal client id (DATABRICKS_CLIENT_ID) MUST win over a hardcoded
    FABRIC_LAKEBASE_USER — Postgres token auth requires the connecting user to match whoever
    actually generated the token, which is the execution identity, never a stray env var
    (Part 7's root cause: a hardcoded human email out-precedenced the real job identity)."""
    env = {"FABRIC_LAKEBASE_USER": "svc-user@example.com",
           "DATABRICKS_CLIENT_ID": _SP_CLIENT_ID}
    assert _resolve_pg_user(env) == _SP_CLIENT_ID


def test_resolve_pg_user_ignores_spark_session_id_on_job_compute():
    """THE 2026-08-10 PROD BUG. Serverless JOB compute sets DATABRICKS_CLIENT_ID to a Spark
    session identifier, not a principal. Connecting as it fails every write with
    ``password authentication failed for user 'spark-...'``. It must be rejected in favour of
    asking the SDK who the run identity really is."""
    env = {"DATABRICKS_CLIENT_ID": "spark-dcd781fd-2241-4bd5-ba14-8c2b3e281ae8"}
    client = _FakeClient(user_name="abdishakur.mohamed@newellco.com")
    assert _resolve_pg_user(env, client) == "abdishakur.mohamed@newellco.com"


def test_resolve_pg_user_uses_sdk_identity_for_a_service_principal_run_as():
    """When run_as is a service principal, current_user.me().user_name is the SP's application
    id — exactly the role Postgres expects."""
    env = {"DATABRICKS_CLIENT_ID": "spark-abc123"}
    assert _resolve_pg_user(env, _FakeClient(user_name=_SP_CLIENT_ID)) == _SP_CLIENT_ID


def test_resolve_pg_user_falls_back_when_sdk_lookup_fails():
    """A workspace-API failure must not crash the connection path — fall through to the
    local-dev override, then to whatever the env had."""
    env = {"DATABRICKS_CLIENT_ID": "spark-abc123",
           "FABRIC_LAKEBASE_USER": "someone@example.com"}
    assert _resolve_pg_user(env, _FakeClient(raises=True)) == "someone@example.com"
    # No override at all -> last resort is the raw env value (previous behavior preserved).
    assert _resolve_pg_user({"DATABRICKS_CLIENT_ID": "spark-abc"},
                            _FakeClient(raises=True)) == "spark-abc"


def test_resolve_pg_user_falls_back_to_local_dev_override():
    """With no execution identity present (e.g. running the CLI on a laptop), FABRIC_LAKEBASE_USER
    is still honored as a local-dev override."""
    env = {"FABRIC_LAKEBASE_USER": "someone@example.com"}
    assert _resolve_pg_user(env) == "someone@example.com"


def test_resolve_pg_user_none_when_neither_set():
    assert _resolve_pg_user({}) is None


# ---------------------------------------------------------------------------
# 16b: create_ticket_writer() reconnect/retry on a dropped connection
# ---------------------------------------------------------------------------

class _RecordingCursor:
    def __init__(self, fail_times=0, exc_cls=None):
        self.calls = []
        self._fail_times = fail_times
        self._exc_cls = exc_cls or Exception

    def execute(self, sql, params):
        if self._fail_times > 0:
            self._fail_times -= 1
            msg = ("server closed the connection unexpectedly"
                   if self._exc_cls is _OperationalError else "invalid input syntax")
            raise self._exc_cls(msg)
        self.calls.append((sql, params))


class _RecordingConn:
    def __init__(self, fail_times=0, exc_cls=None):
        self.cur = _RecordingCursor(fail_times=fail_times, exc_cls=exc_cls)
        self.committed = 0
        self.closed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed += 1

    def close(self):
        self.closed = True


class _OperationalError(Exception):
    """Stand-in for psycopg2.OperationalError — matched by class name, not by import (16b)."""


def _meta(key="incident-1"):
    return {"incidentKey": key, "checkType": "throttle", "severity": "warn",
            "resource": "R", "workspace": "W", "detail": "d",
            "firstDetected": "2026-08-07T00:00:00Z", "currentlyActive": True}


def test_write_reconnects_and_retries_once_on_transient_connection_error():
    """A write that hits a dropped connection (OperationalError) reconnects once and succeeds."""
    bad_conn = _RecordingConn(fail_times=1, exc_cls=_OperationalError)
    good_conn = _RecordingConn()
    conns = iter([bad_conn, good_conn])

    writer = create_ticket_writer(connect=lambda: next(conns))
    writer("chat-1", _meta())  # first _conn() call opens bad_conn

    assert bad_conn.committed == 0          # failed before commit
    assert good_conn.committed == 1         # succeeded on the fresh connection
    assert len(good_conn.cur.calls) == 1


def test_write_raises_clear_error_after_persistent_connection_failure():
    """A connection error that persists across the one allowed retry raises LakebaseWriteError
    (not a bare driver exception, not a hang) after exactly 2 attempts."""
    always_bad = lambda: _RecordingConn(fail_times=1, exc_cls=_OperationalError)
    writer = create_ticket_writer(connect=always_bad)

    with pytest.raises(LakebaseWriteError) as exc_info:
        writer("chat-1", _meta())
    assert "incident-1" in str(exc_info.value)
    assert "2 attempts" in str(exc_info.value)


def test_write_does_not_retry_non_connection_errors():
    """A non-connection failure (e.g. a constraint/SQL error) must not be retried — it should raise
    immediately, on the first attempt, without opening a second connection."""
    conn = _RecordingConn(fail_times=1, exc_cls=ValueError)
    opened = {"n": 0}

    def _connect():
        opened["n"] += 1
        return conn

    writer = create_ticket_writer(connect=_connect)
    with pytest.raises(LakebaseWriteError) as exc_info:
        writer("chat-1", _meta())
    assert opened["n"] == 1  # never reconnected
    assert "1 attempt" in str(exc_info.value)


def test_write_succeeds_on_first_try_with_injected_conn():
    """Baseline: no failure at all -> single attempt, single commit, correct SQL shape."""
    conn = _RecordingConn()
    writer = create_ticket_writer(conn=conn)
    writer("chat-1", _meta("incident-2"))

    assert conn.committed == 1
    (sql, params), = conn.cur.calls
    assert "alert_ticket" in sql and "ON CONFLICT" in sql
    assert params[0] == "incident-2" and params[1] == "chat-1"
