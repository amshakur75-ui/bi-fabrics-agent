"""Tier-2 alert state store — the ``audit_alerts`` Delta table (dedup / 48h reminders / resolution).

The store exposes ``{"query_active", "upsert", "resolve"}``. Production MUST use the Delta store
(``create_alerts_store_delta``) so incident state persists across the 5-minute job invocations;
``create_alerts_store_memory`` is for tests and offline runs only (no cross-run persistence).

Alert dicts are camelCase (project convention); Delta columns are snake_case — mapped by
``_to_row`` / ``_from_row``.

IMPORTANT: ``_FIELDS`` is the ONLY thing that persists. ``_to_row`` builds the Delta row by
iterating ``_FIELDS``, so any key written onto an alert row that is not listed there is
silently discarded on write and comes back ``None`` on the next read. The in-memory store
below keeps the whole dict, so tests that only use it will NOT catch a missing field —
see ``tests/test_alerts_store_delta_fidelity.py`` for the round-trip regression guard.
"""
import json

_FIELDS = [
    ("incidentKey", "incident_key"),
    ("status", "status"),
    ("severity", "severity"),
    ("checkType", "check_type"),
    ("resource", "resource"),
    ("chatId", "chat_id"),
    ("metric", "metric"),
    ("firstAlertedAt", "first_alerted_at"),
    ("lastAlertedAt", "last_alerted_at"),
    ("lastRemindedAt", "last_reminded_at"),
    ("resolvedAt", "resolved_at"),
    ("escalationCount", "escalation_count"),
    ("materialityReason", "materiality_reason"),
    ("investigationSummary", "investigation_summary"),
    ("delivered", "delivered"),
    ("runAt", "run_at"),
    ("currentlyActive", "currently_active"),
    ("presenceCount", "presence_count"),
    # Design A' (2026-08-09) capacity-incident state. These MUST be here: ``_to_row`` drops any
    # key not listed, so a field written onto the row by ``process_alerts`` but missing from
    # ``_FIELDS`` silently vanishes on the Delta write and reads back as None next tick.
    # ``absenceCount`` powers quiet-to-resolve (without it an incident never auto-resolves);
    # ``signalTypes`` powers "a new signal joined" escalation (without it EVERY tick looks like
    # a new signal, so a card fires every 5 minutes); ``throttleMinutes`` powers the
    # throttle-worsened escalation comparison.
    ("absenceCount", "absence_count"),
    ("signalTypes", "signal_types"),
    ("throttleMinutes", "throttle_minutes"),
    # Burndown is the FOURTH escalation axis: overage draining far slower than before is an
    # imminent worsening the peak / throttle / signal-set axes cannot see. Needs the PREVIOUS
    # reading to detect the collapse, so it must persist.
    ("minutesToBurndown", "minutes_to_burndown"),
]

# Fields carried as a Python list but stored as a compact JSON string (Delta STRING column),
# so the row stays a flat scalar schema and an analyst can from_json() it.
_JSON_LIST_FIELDS = ("signalTypes",)


def _to_row(alert):
    """camelCase alert dict -> snake_case Delta row (all columns present, missing -> None)."""
    row = {col: alert.get(cc) for cc, col in _FIELDS}
    for cc, col in _FIELDS:
        if cc in _JSON_LIST_FIELDS:
            v = row.get(col)
            if v is not None and not isinstance(v, str):
                row[col] = json.dumps(list(v), separators=(",", ":"))
    return row


def _from_row(row):
    """snake_case Delta row (dict) -> camelCase alert dict."""
    out = {cc: row.get(col) for cc, col in _FIELDS}
    for cc in _JSON_LIST_FIELDS:
        v = out.get(cc)
        if isinstance(v, str):
            try:
                decoded = json.loads(v)
            except (TypeError, ValueError):
                decoded = []
            # Coerce a successfully-decoded NON-list to []. `set("throttle")` would iterate
            # CHARACTERS, making every signal look new (card every tick); a non-iterable would
            # raise inside is_escalation and get swallowed, silencing EVERY alert that sweep.
            out[cc] = decoded if isinstance(decoded, list) else []
        elif v is not None and not isinstance(v, list):
            out[cc] = []
    return out


def create_alerts_store_memory(initial=None):
    """In-memory store keyed by incidentKey. Tests/offline only — no cross-run persistence."""
    data = {k: dict(v) for k, v in (initial or {}).items()}

    def query_active():
        return {k: dict(v) for k, v in data.items() if v.get("status") == "active"}

    def query_pending():
        return {k: dict(v) for k, v in data.items() if v.get("status") == "pending"}

    def query_informational():
        return {k: dict(v) for k, v in data.items() if v.get("status") == "informational"}

    def upsert(alert):
        data[alert["incidentKey"]] = dict(alert)

    def resolve(incident_key, at):
        cur = data.get(incident_key)
        if cur is not None and cur.get("status") == "active":
            cur["status"] = "resolved"
            cur["resolvedAt"] = at
            cur["runAt"] = at

    def delete(incident_key):
        data.pop(incident_key, None)

    return {"query_active": query_active, "query_pending": query_pending,
            "query_informational": query_informational, "upsert": upsert,
            "resolve": resolve, "delete": delete, "_data": data}


def _schema():
    try:
        from pyspark.sql.types import (
            StructType, StructField, StringType, DoubleType, IntegerType, BooleanType,
        )
        t = {"metric": DoubleType(), "escalation_count": IntegerType(),
             "delivered": BooleanType(), "currently_active": BooleanType(),
             "presence_count": IntegerType(),
             # Design A'. signal_types intentionally stays StringType (JSON-encoded by
             # _to_row) — handing createDataFrame a raw Python list against a StringType
             # column raises TypeError inside upsert(), which has no try/except and would
             # drop the alert outright.
             "absence_count": IntegerType(), "throttle_minutes": DoubleType(),
             "minutes_to_burndown": DoubleType()}
        return StructType([
            StructField(col, t.get(col, StringType()), True) for _, col in _FIELDS
        ])
    except ImportError:
        return None


def create_alerts_store_delta(catalog, schema, *, spark=None):
    """Delta-backed store on ``audit_alerts`` (Spark MERGE upsert). Use in production."""
    table = f"`{catalog}`.`{schema}`.audit_alerts"

    _ensured = {"done": False}
    # SQL type per snake_case column, for the self-heal ALTER below (default STRING).
    _COL_SQL_TYPE = {"metric": "DOUBLE", "escalation_count": "INT", "delivered": "BOOLEAN",
                     "currently_active": "BOOLEAN", "presence_count": "INT",
                     # Design A' — the self-heal ALTER below adds these to an existing
                     # prod table on first use, so no manual migration is needed.
                     "absence_count": "INT", "throttle_minutes": "DOUBLE",
                     "minutes_to_burndown": "DOUBLE"}

    def _get_spark():
        nonlocal spark
        if spark is not None:
            return spark
        from pyspark.sql import SparkSession
        spark = SparkSession.getActiveSession()
        if spark is None:
            raise RuntimeError("No active SparkSession")
        return spark

    def _ensure_schema(s):
        """Deterministically add any missing ``_FIELDS`` column to the table (once per store).

        Relying on Delta's MERGE autoMerge to evolve the schema proved unreliable in the serverless
        job — the ``currently_active`` / ``presence_count`` columns silently never got added, which
        left hysteresis unable to persist its streak counter (so attribution never promoted to an
        alert). An explicit, idempotent ALTER is the robust fix. Never fatal: a failure just leaves
        the missing column absent (degrades, doesn't crash the run)."""
        if _ensured["done"]:
            return
        try:
            existing = {r["col_name"] for r in s.sql(f"DESCRIBE TABLE {table}").collect()}
            for _, col in _FIELDS:
                if col not in existing:
                    s.sql(f"ALTER TABLE {table} ADD COLUMNS ({col} {_COL_SQL_TYPE.get(col, 'STRING')})")
        except Exception as exc:
            print(f"[alerts] schema self-heal skipped ({type(exc).__name__}: {exc})")
        _ensured["done"] = True

    def query_active():
        s = _get_spark()
        _ensure_schema(s)
        rows = s.sql(f"SELECT * FROM {table} WHERE status = 'active'").collect()
        return {r["incident_key"]: _from_row(r.asDict()) for r in rows}

    def query_pending():
        s = _get_spark()
        _ensure_schema(s)
        rows = s.sql(f"SELECT * FROM {table} WHERE status = 'pending'").collect()
        return {r["incident_key"]: _from_row(r.asDict()) for r in rows}

    def query_informational():
        s = _get_spark()
        _ensure_schema(s)
        rows = s.sql(f"SELECT * FROM {table} WHERE status = 'informational'").collect()
        return {r["incident_key"]: _from_row(r.asDict()) for r in rows}

    def upsert(alert):
        s = _get_spark()
        _ensure_schema(s)
        # Belt-and-suspenders: also request MERGE autoMerge, but the explicit _ensure_schema above is
        # what actually guarantees the columns exist (autoMerge proved unreliable here).
        try:
            s.sql("SET spark.databricks.delta.schema.autoMerge.enabled = true")
        except Exception:
            pass
        df = s.createDataFrame([_to_row(alert)], schema=_schema())
        df.createOrReplaceTempView("_audit_alert_upsert")
        s.sql(
            f"MERGE INTO {table} t USING _audit_alert_upsert s "
            "ON t.incident_key = s.incident_key "
            "WHEN MATCHED THEN UPDATE SET * "
            "WHEN NOT MATCHED THEN INSERT *"
        )

    def resolve(incident_key, at):
        s = _get_spark()
        safe = str(incident_key).replace("'", "''")
        safe_at = str(at).replace("'", "''")
        s.sql(
            f"UPDATE {table} SET status = 'resolved', resolved_at = '{safe_at}', "
            f"run_at = '{safe_at}' WHERE incident_key = '{safe}' AND status = 'active'"
        )

    def delete(incident_key):
        s = _get_spark()
        safe = str(incident_key).replace("'", "''")
        s.sql(f"DELETE FROM {table} WHERE incident_key = '{safe}'")

    # query_informational MUST be exported. `daily_summary` reads it defensively via
    # `alerts_store.get("query_informational", lambda: {})()`, so omitting it here silently
    # resolved to `{}` in PRODUCTION — every informational pattern was written to Delta and never
    # read back, leaving the digest's informational section permanently empty. The memory store
    # did export it, which is exactly why no test caught the divergence.
    return {"query_active": query_active, "query_pending": query_pending,
            "query_informational": query_informational, "upsert": upsert,
            "resolve": resolve, "delete": delete}
