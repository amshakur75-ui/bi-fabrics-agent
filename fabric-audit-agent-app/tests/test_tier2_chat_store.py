"""Lakebase alert-chat writer — SQL/row shape via an injected fake connection (no live DB)."""
import json

from fabric_audit_agent.adapters.chat_store_lakebase import create_alert_chat


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
