"""Tier-2 alert state store — the ``audit_alerts`` Delta table (dedup / 48h reminders / resolution).

The store exposes ``{"query_active", "upsert", "resolve"}``. Production MUST use the Delta store
(``create_alerts_store_delta``) so incident state persists across the 5-minute job invocations;
``create_alerts_store_memory`` is for tests and offline runs only (no cross-run persistence).

Alert dicts are camelCase (project convention); Delta columns are snake_case — mapped by
``_to_row`` / ``_from_row``.
"""

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
]


def _to_row(alert):
    """camelCase alert dict -> snake_case Delta row (all columns present, missing -> None)."""
    return {col: alert.get(cc) for cc, col in _FIELDS}


def _from_row(row):
    """snake_case Delta row (dict) -> camelCase alert dict."""
    return {cc: row.get(col) for cc, col in _FIELDS}


def create_alerts_store_memory(initial=None):
    """In-memory store keyed by incidentKey. Tests/offline only — no cross-run persistence."""
    data = {k: dict(v) for k, v in (initial or {}).items()}

    def query_active():
        return {k: dict(v) for k, v in data.items() if v.get("status") == "active"}

    def query_pending():
        return {k: dict(v) for k, v in data.items() if v.get("status") == "pending"}

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

    return {"query_active": query_active, "query_pending": query_pending, "upsert": upsert,
            "resolve": resolve, "delete": delete, "_data": data}


def _schema():
    try:
        from pyspark.sql.types import (
            StructType, StructField, StringType, DoubleType, IntegerType, BooleanType,
        )
        t = {"metric": DoubleType(), "escalation_count": IntegerType(),
             "delivered": BooleanType(), "currently_active": BooleanType(),
             "presence_count": IntegerType()}
        return StructType([
            StructField(col, t.get(col, StringType()), True) for _, col in _FIELDS
        ])
    except ImportError:
        return None


def create_alerts_store_delta(catalog, schema, *, spark=None):
    """Delta-backed store on ``audit_alerts`` (Spark MERGE upsert). Use in production."""
    table = f"`{catalog}`.`{schema}`.audit_alerts"

    def _get_spark():
        nonlocal spark
        if spark is not None:
            return spark
        from pyspark.sql import SparkSession
        spark = SparkSession.getActiveSession()
        if spark is None:
            raise RuntimeError("No active SparkSession")
        return spark

    def query_active():
        s = _get_spark()
        rows = s.sql(f"SELECT * FROM {table} WHERE status = 'active'").collect()
        return {r["incident_key"]: _from_row(r.asDict()) for r in rows}

    def query_pending():
        s = _get_spark()
        rows = s.sql(f"SELECT * FROM {table} WHERE status = 'pending'").collect()
        return {r["incident_key"]: _from_row(r.asDict()) for r in rows}

    def upsert(alert):
        s = _get_spark()
        # Auto-evolve the table schema so newly-added columns (e.g. currently_active) don't require a
        # manual ALTER — the MERGE below adds them on first write.
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

    return {"query_active": query_active, "query_pending": query_pending, "upsert": upsert,
            "resolve": resolve, "delete": delete}
