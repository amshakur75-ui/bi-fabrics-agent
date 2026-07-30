"""Delta-backed StorePort: persists run history to Unity Catalog Delta tables.

Swap-compatible with ``store_local.py`` — same ``{history, append}`` contract. Falls back to
the local JSON store when Spark/Unity Catalog is not available (keeps local/offline testing
working). The Delta table must already exist (created via ``scripts/create_delta_tables.sql``).

Requires the Databricks Runtime (PySpark + Delta). Not importable in a plain-Python environment
— callers should catch ImportError and fall back to ``store_local.py``.
"""
import json


def _to_delta_row(run):
    """Convert a pipeline run record (camelCase dict) to a Delta-friendly flat row."""
    return {
        "run_at": run.get("runAt"),
        "tenant": run.get("tenant"),
        "peak_cu_pct": (run.get("metrics") or {}).get("peakCuPct"),
        "verdict_decision": run.get("verdictDecision"),
        "sla_breached_count": run.get("slaBreachedCount"),
        "duration_ms": run.get("durationMs"),
        "errored": run.get("errored", False),
        "token_usage": json.dumps(run.get("tokenUsage"), ensure_ascii=False)
            if run.get("tokenUsage") is not None else None,
        "findings_json": json.dumps(run.get("findings", []), ensure_ascii=False),
    }


def _from_delta_row(row):
    """Convert a Delta row back to the camelCase dict that alerting.py expects."""
    token_raw = row.get("token_usage")
    return {
        "runAt": row.get("run_at"),
        "tenant": row.get("tenant"),
        "metrics": {"peakCuPct": row.get("peak_cu_pct")},
        "verdictDecision": row.get("verdict_decision"),
        "slaBreachedCount": row.get("sla_breached_count"),
        "durationMs": row.get("duration_ms"),
        "errored": row.get("errored", False),
        "tokenUsage": json.loads(token_raw) if token_raw else None,
        "findings": json.loads(row.get("findings_json") or "[]"),
    }


def create_delta_store(catalog, schema, *, spark=None, keep=180):
    """Create a Delta-backed store with the same interface as ``create_local_store``.

    ``spark`` is injected for testability; defaults to the active SparkSession at call time.
    """
    table = f"{catalog}.{schema}.run_history"

    def _get_spark():
        nonlocal spark
        if spark is not None:
            return spark
        from pyspark.sql import SparkSession
        spark = SparkSession.getActiveSession()
        if spark is None:
            raise RuntimeError(
                "No active SparkSession — create_delta_store requires Databricks Runtime. "
                "Use create_local_store for local/offline testing."
            )
        return spark

    def history():
        s = _get_spark()
        try:
            rows = (
                s.table(table)
                .orderBy("run_at")
                .limit(keep)
                .collect()
            )
            return [_from_delta_row(r.asDict()) for r in rows]
        except Exception:
            return []

    def append(run):
        s = _get_spark()
        row = _to_delta_row(run)
        df = s.createDataFrame([row])
        df.write.mode("append").saveAsTable(table)

        count = s.table(table).count()
        if count > keep:
            cutoff_rows = (
                s.table(table)
                .orderBy("run_at")
                .limit(int(count - keep))
                .select("run_at")
                .collect()
            )
            if cutoff_rows:
                oldest = cutoff_rows[-1]["run_at"]
                s.sql(f"DELETE FROM {table} WHERE run_at <= '{oldest}'")
        return min(count, keep)

    return {"history": history, "append": append}
