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


def create_ack_store(*, conn=None):
    """Ack/snooze store over ``ai_chatbot.alert_ack`` (6c). The chat app writes acks/snoozes there;
    the Tier-2 job reads them each run to suppress reminders. Snapshots the whole (tiny) table ONCE
    and serves ``get(chat_id) -> {"status","snoozeUntil"} | None`` from memory (no per-incident
    round-trip). ``conn`` injectable for tests. Raises on connect/query failure so the caller can
    fail-open (ack is an optional enhancement, never a reason to drop a real alert)."""
    snapshot = {}
    owns = conn is None
    c = conn if conn is not None else _lakebase_conn()
    try:
        cur = c.cursor()
        cur.execute('SELECT chat_id, status, snooze_until, resolution_note FROM ai_chatbot.alert_ack')
        for cid, status, until, note in cur.fetchall():
            snapshot[cid] = {"status": status,
                             "snoozeUntil": until.isoformat() if until is not None else None,
                             "resolutionNote": note}
    finally:
        if owns:
            try:
                c.close()
            except Exception:
                pass

    def reopen(chat_id):
        """Clear a ticket's ack/resolve state (Step 8 auto-reopen on recurrence) so reminders +
        alerts resume. Opens its own short-lived connection (rare path)."""
        rc = conn if conn is not None else _lakebase_conn()
        try:
            cur2 = rc.cursor()
            cur2.execute('DELETE FROM ai_chatbot.alert_ack WHERE chat_id = %s', (chat_id,))
            rc.commit()
            snapshot.pop(chat_id, None)
        finally:
            if conn is None:
                try:
                    rc.close()
                except Exception:
                    pass

    return {"get": lambda chat_id: snapshot.get(chat_id), "reopen": reopen}


def create_ticket_writer(*, conn=None):
    """Return a writer ``fn(chat_id, meta)`` that upserts a row into ``ai_chatbot.alert_ticket``
    (Step 9). The Tier-2 job WRITES this; the chat app READS it to show ticket detail (what / where /
    since when / currently active) in the Alerts sidebar — the reverse of the ``alert_ack`` boundary.

    Keyed by ``meta["incidentKey"]`` (the PRIMARY KEY), NOT ``chat_id`` — ``chat_id`` is a nullable
    column. This is the Part-7 fix: when chat creation upstream fails, callers still invoke this
    writer with ``chat_id=None`` so the finding's ticket lands in the table (a None chat_id can't be
    a row's identity, since a table keyed by chat_id would silently drop it — see tightening.md
    Part 7). ``meta`` keys: incidentKey, checkType, severity, resource, workspace, detail,
    firstDetected (ISO text), currentlyActive. One connection is opened lazily and reused for the
    whole run (the tier2 wheel task exits after one run). ``conn`` injectable for tests. Raises on
    the FIRST connect so the caller can fail-open; per-write errors surface to the caller, which
    swallows them (ticket metadata is best-effort — never a reason to drop a real alert)."""
    state = {"conn": conn}

    def _conn():
        if state["conn"] is None:
            state["conn"] = _lakebase_conn()
        return state["conn"]

    def write(chat_id, meta):
        incident_key = meta.get("incidentKey") or chat_id  # defensive: always need SOME stable key
        c = _conn()
        cur = c.cursor()
        cur.execute(
            "INSERT INTO ai_chatbot.alert_ticket "
            "(incident_key, chat_id, check_type, severity, resource, workspace, detail, "
            "first_detected, currently_active, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (incident_key) DO UPDATE SET "
            "chat_id = excluded.chat_id, check_type = excluded.check_type, "
            "severity = excluded.severity, resource = excluded.resource, "
            "workspace = excluded.workspace, detail = excluded.detail, "
            "first_detected = excluded.first_detected, "
            "currently_active = excluded.currently_active, updated_at = now()",
            (incident_key, chat_id, meta.get("checkType"), meta.get("severity"),
             meta.get("resource"), meta.get("workspace"), meta.get("detail"),
             meta.get("firstDetected"), meta.get("currentlyActive")),
        )
        c.commit()

    return write


def _endpoint_path():
    """Resolve the Lakebase **endpoint** resource path used for the credential request.

    This project is an **Autoscaling** Lakebase project, so credentials are issued per endpoint
    (``projects/<id>/branches/<branch>/endpoints/<endpoint>``), not per Provisioned instance.
    ``FABRIC_LAKEBASE_ENDPOINT_PATH`` overrides everything; otherwise the path is built from
    ``FABRIC_LAKEBASE_INSTANCE`` + branch (default ``production``) + endpoint id (default ``primary``).
    """
    override = os.environ.get("FABRIC_LAKEBASE_ENDPOINT_PATH")
    if override:
        return override
    instance = os.environ.get("FABRIC_LAKEBASE_INSTANCE", "fabrics-audit-agent-memory")
    branch = os.environ.get("FABRIC_LAKEBASE_BRANCH", "production")
    endpoint = os.environ.get("FABRIC_LAKEBASE_ENDPOINT_ID", "primary")
    return f"projects/{instance}/branches/{branch}/endpoints/{endpoint}"


def _lakebase_conn(*, client=None, connect=None):
    """Build a psycopg connection to Lakebase using the run identity's DB credential.

    Reads FABRIC_LAKEBASE_HOST / FABRIC_LAKEBASE_DB / FABRIC_LAKEBASE_USER; the password is a
    short-lived token from the **Autoscaling** postgres credential API
    (``w.postgres.generate_database_credential(<endpoint path>)``). ``client`` (a WorkspaceClient)
    and ``connect`` (``psycopg2.connect``) are injectable for tests; both default to the real ones.
    """
    host = os.environ["FABRIC_LAKEBASE_HOST"]
    db = os.environ.get("FABRIC_LAKEBASE_DB", "databricks_postgres")
    user = os.environ.get("FABRIC_LAKEBASE_USER") or os.environ.get("DATABRICKS_CLIENT_ID")
    if client is None:
        from databricks.sdk import WorkspaceClient
        client = WorkspaceClient()
    cred = client.postgres.generate_database_credential(_endpoint_path())
    token = getattr(cred, "token", None) or cred["token"]
    if connect is None:
        import psycopg2  # job dependency
        connect = psycopg2.connect
    return connect(host=host, port=5432, dbname=db, user=user,
                   password=token, sslmode="require")
