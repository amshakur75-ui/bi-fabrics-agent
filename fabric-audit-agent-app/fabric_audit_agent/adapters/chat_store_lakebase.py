"""Pre-create a saved chat conversation in Lakebase ``ai_chatbot`` (the alert deep-link target).

The Tier-2 job calls ``create_alert_chat`` to insert a public, system-owned ``Chat`` + one
assistant ``Message`` (the investigation) so the Teams card can deep-link to ``/chat/<id>`` and the
team can continue it. Column names mirror the Drizzle schema exactly (camelCase, quoted).

Auth/connection is isolated in ``_lakebase_conn`` (validated live at deploy); tests inject ``conn``.
"""
import json
import os
import uuid
from datetime import datetime, timezone

SYSTEM_USER_ID = "fabric-audit-agent"


def _now():
    return datetime.now(timezone.utc)


def create_alert_chat(markdown, title, *, conn=None, user_id=SYSTEM_USER_ID, now=None):
    """Insert a public Chat + one assistant Message (parts=[{text: markdown}]). Returns chat_id.

    ``conn`` is a DB-API connection (psycopg); when None, a Lakebase connection is built. The two
    inserts run in one transaction.
    """
    chat_id = str(uuid.uuid4())
    msg_id = str(uuid.uuid4())
    ts = now if now is not None else _now()
    parts = json.dumps([{"type": "text", "text": markdown}], ensure_ascii=False)

    owns = conn is None
    if owns:
        conn = _lakebase_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO ai_chatbot."Chat" (id, "createdAt", title, "userId", visibility) '
            "VALUES (%s, %s, %s, %s, %s)",
            (chat_id, ts, title, user_id, "public"),
        )
        cur.execute(
            'INSERT INTO ai_chatbot."Message" (id, "chatId", role, parts, attachments, "createdAt") '
            "VALUES (%s, %s, %s, %s::json, %s::json, %s)",
            (msg_id, chat_id, "assistant", parts, "[]", ts),
        )
        conn.commit()
    finally:
        if owns:
            try:
                conn.close()
            except Exception:
                pass
    return chat_id


def _lakebase_conn():
    """Build a psycopg connection to Lakebase using the run identity's DB credential.

    Reads FABRIC_LAKEBASE_HOST / FABRIC_LAKEBASE_DB / FABRIC_LAKEBASE_USER; the password is a
    short-lived token from the databricks-sdk postgres credential API. Validated live at deploy.
    """
    import psycopg2  # job dependency
    from databricks.sdk import WorkspaceClient

    host = os.environ["FABRIC_LAKEBASE_HOST"]
    db = os.environ.get("FABRIC_LAKEBASE_DB", "databricks_postgres")
    user = os.environ.get("FABRIC_LAKEBASE_USER") or os.environ.get("DATABRICKS_CLIENT_ID")
    instance = os.environ.get("FABRIC_LAKEBASE_INSTANCE", "fabrics-audit-agent-memory")
    w = WorkspaceClient()
    cred = w.database.generate_database_credential(request_id=str(uuid.uuid4()),
                                                   instance_names=[instance])
    token = getattr(cred, "token", None) or cred["token"]
    return psycopg2.connect(host=host, port=5432, dbname=db, user=user,
                            password=token, sslmode="require")
