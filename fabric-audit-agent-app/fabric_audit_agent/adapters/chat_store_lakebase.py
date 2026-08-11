"""Pre-create a saved chat conversation in Lakebase ``ai_chatbot`` (the alert deep-link target).

The Tier-2 job calls ``create_alert_chat`` to insert a public, system-owned ``Chat`` + one
assistant ``Message`` (the investigation) so the Teams card can deep-link to ``/chat/<id>`` and the
team can continue it. Column names mirror the Drizzle schema exactly (camelCase, quoted).

Auth/connection is isolated in ``_lakebase_conn`` (validated live at deploy); tests inject ``conn``.
"""
import json
import os
import re
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
        # Keyed by BOTH handles. The app writes an ack for a chat-less ticket under `incident_key`
        # (there is no chat to key on), but this only ever selected `chat_id` -- so every such ack
        # collapsed onto key None and was unreadable. The visible consequence is at the Step-8
        # recurrence-after-resolve branch: a chat-less incident that a human resolved and which then
        # RECURS could never be reopened or re-alerted with the prior resolution note, because the
        # lookup could not find the ack that says it was resolved.
        cur.execute('SELECT chat_id, incident_key, status, snooze_until, resolution_note '
                    'FROM ai_chatbot.alert_ack')
        for cid, ikey, status, until, note in cur.fetchall():
            entry = {"status": status,
                     "snoozeUntil": until.isoformat() if until is not None else None,
                     "resolutionNote": note}
            if cid is not None:
                snapshot[cid] = entry
            if ikey is not None:
                snapshot[ikey] = entry
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
            # Match on EITHER handle: the caller may hold a chat id or an incident key, and a
            # chat-less ack has no chat_id at all, so a chat_id-only DELETE silently removed nothing
            # and the ticket stayed resolved forever despite recurring.
            cur2.execute('DELETE FROM ai_chatbot.alert_ack '
                         'WHERE chat_id = %s OR incident_key = %s', (chat_id, chat_id))
            rc.commit()
            snapshot.pop(chat_id, None)
        finally:
            if conn is None:
                try:
                    rc.close()
                except Exception:
                    pass

    return {"get": lambda chat_id: snapshot.get(chat_id), "reopen": reopen}


class LakebaseWriteError(RuntimeError):
    """Raised by ``create_ticket_writer``'s ``write()`` when a Postgres write still fails after the
    dropped-connection retry (16b). Callers already fail-open around ticket writes (they catch
    ``Exception`` and log); this exists so that failure surfaces with a clear, actionable message
    rather than a bare driver exception or (worse) a call that silently hangs on a dead socket."""


def _is_pg_connection_error(exc):
    """True iff *exc* looks like a dropped/broken Postgres connection — psycopg2's
    ``OperationalError``/``InterfaceError`` (raised for a lost connection, closed socket, etc.) or a
    "connection ... closed" message — matched by class name + message so psycopg2 need not be
    imported here (it's an optional job dependency, same pattern as ``clients._la_is_transient``).
    A non-connection error (e.g. a SQL/constraint error) returns False so it is never retried."""
    if type(exc).__name__ in ("OperationalError", "InterfaceError"):
        return True
    msg = str(exc).lower()
    return "connection" in msg and ("closed" in msg or "lost" in msg or "terminat" in msg)


def create_ticket_writer(*, conn=None, connect=None):
    """Return a writer ``fn(chat_id, meta)`` that upserts a row into ``ai_chatbot.alert_ticket``
    (Step 9). The Tier-2 job WRITES this; the chat app READS it to show ticket detail (what / where /
    since when / currently active) in the Alerts sidebar — the reverse of the ``alert_ack`` boundary.

    Keyed by ``meta["incidentKey"]`` (the PRIMARY KEY), NOT ``chat_id`` — ``chat_id`` is a nullable
    column. This is the Part-7 fix: when chat creation upstream fails, callers still invoke this
    writer with ``chat_id=None`` so the finding's ticket lands in the table (a None chat_id can't be
    a row's identity, since a table keyed by chat_id would silently drop it — see tightening.md
    Part 7). ``meta`` keys: incidentKey, checkType, severity, resource, workspace, detail,
    firstDetected (ISO text), currentlyActive. One connection is opened lazily and reused for the
    whole run (the tier2 wheel task exits after one run). ``conn`` injectable for tests, ``connect``
    (a zero-arg connection factory, default ``_lakebase_conn``) also injectable so tests can control
    reconnects. Raises on the FIRST connect so the caller can fail-open.

    16b: if a write hits a dropped/broken connection (``_is_pg_connection_error``), the writer
    discards the stale connection, opens exactly one fresh one via ``connect``, and retries the same
    write once. A second failure (connection or otherwise) raises ``LakebaseWriteError`` with a
    clear message instead of a bare driver exception — the caller's existing try/except still
    swallows it (ticket metadata is best-effort, never a reason to drop a real alert)."""
    _connect = connect if connect is not None else _lakebase_conn
    state = {"conn": conn}

    def _conn():
        if state["conn"] is None:
            state["conn"] = _connect()
        return state["conn"]

    def write(chat_id, meta):
        incident_key = meta.get("incidentKey") or chat_id  # defensive: always need SOME stable key
        last_exc = None
        for attempt in range(2):  # first attempt + one reconnect retry
            c = _conn()
            try:
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
                return
            except Exception as exc:  # noqa: BLE001 — classified immediately below
                last_exc = exc
                if attempt == 1 or not _is_pg_connection_error(exc):
                    break
                state["conn"] = None  # stale/broken connection: drop it, reconnect on next _conn()

        raise LakebaseWriteError(
            f"Lakebase ticket write failed for incident {incident_key!r} after "
            f"{'2 attempts (reconnect retry exhausted)' if _is_pg_connection_error(last_exc) else '1 attempt'}"
            f": {type(last_exc).__name__}: {last_exc}"
        ) from last_exc

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


_CLIENT_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _resolve_pg_user(env, client=None):
    """Resolve the Postgres connecting user for a Lakebase token-auth connection (16a).

    Postgres token auth requires the connecting ``user`` to match the identity that generated
    the token. ``generate_database_credential()`` always mints a token for the identity actually
    running the code, so the connecting user must be that same principal.

    ``DATABRICKS_CLIENT_ID`` is the right answer in the App runtime, where it holds the App's
    service-principal client id (a UUID). It is the WRONG answer on serverless JOB compute,
    which sets it to a Spark session identifier like ``spark-dcd781fd-2241-4bd5-ba14-8c...``.
    Postgres has no such role, so every ticket/ack write failed with::

        password authentication failed for user 'spark-dcd781fd-2241-4bd5-ba14-8c'

    (observed live on the tier2 job, 2026-08-10). So the env var is only trusted when it is
    actually shaped like a client id; otherwise we ask the SDK who we really are, which returns
    the SP's application id for a service principal and the user name for a human run_as —
    exactly what Postgres expects in both cases. ``FABRIC_LAKEBASE_USER`` remains the last-
    resort local-dev override for a laptop with no execution identity at all.
    """
    cid = env.get("DATABRICKS_CLIENT_ID")
    if cid and _CLIENT_ID_RE.match(cid):
        return cid
    if client is not None:
        try:
            who = client.current_user.me()
            name = getattr(who, "user_name", None)
            if name:
                return name
        except Exception as exc:
            print(f"[lakebase] current_user lookup failed ({type(exc).__name__}: {exc})")
    return env.get("FABRIC_LAKEBASE_USER") or cid


def _lakebase_conn(*, client=None, connect=None):
    """Build a psycopg connection to Lakebase using the run identity's DB credential.

    Reads FABRIC_LAKEBASE_HOST / FABRIC_LAKEBASE_DB; the connecting user is resolved by
    ``_resolve_pg_user`` (execution identity first, local-dev override second — 16a). The password
    is a short-lived token from the **Autoscaling** postgres credential API
    (``w.postgres.generate_database_credential(<endpoint path>)``). ``client`` (a WorkspaceClient)
    and ``connect`` (``psycopg2.connect``) are injectable for tests; both default to the real ones.
    """
    host = os.environ["FABRIC_LAKEBASE_HOST"]
    db = os.environ.get("FABRIC_LAKEBASE_DB", "databricks_postgres")
    if client is None:
        from databricks.sdk import WorkspaceClient
        client = WorkspaceClient()
    # Client is built BEFORE resolving the user: on job compute the env var is a Spark session
    # id, so _resolve_pg_user needs the SDK to ask who the run identity actually is.
    user = _resolve_pg_user(os.environ, client)
    cred = client.postgres.generate_database_credential(_endpoint_path())
    token = getattr(cred, "token", None) or cred["token"]
    if connect is None:
        import psycopg2  # job dependency
        connect = psycopg2.connect
    return connect(host=host, port=5432, dbname=db, user=user,
                   password=token, sslmode="require")
