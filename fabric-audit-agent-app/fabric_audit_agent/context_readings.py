"""Tier-2 rolling-readings store — the ``tier2_readings`` Delta table.

Append-only history of what each 5-minute Tier-2 run saw (peak CU%, throttle, item count, whether
the collector succeeded). The STATEFUL gates (sustained-band, rate-of-change, silent-failure) read
the last N readings to reason across runs — impossible from a single pull. The store exposes
``{"append", "recent"}``; production uses ``create_readings_store_delta`` so history survives across
the 5-minute job invocations, ``create_readings_store_memory`` is for tests/offline.

Reading dicts are camelCase (project convention); Delta columns are snake_case — mapped by
``_to_row`` / ``_from_row``.
"""

_FIELDS = [
    ("runAt", "run_at"),
    ("peakCuPct", "peak_cu_pct"),
    ("throttleMinutes", "throttle_minutes"),
    ("itemCount", "item_count"),
    ("collectorOk", "collector_ok"),
]


def _to_row(reading):
    return {col: reading.get(cc) for cc, col in _FIELDS}


def _from_row(row):
    return {cc: row.get(col) for cc, col in _FIELDS}


def create_readings_store_memory(initial=None):
    """In-memory append-only store. Tests/offline only — no cross-run persistence."""
    data = [dict(r) for r in (initial or [])]

    def append(reading):
        data.append(dict(reading))

    def recent(n=12):
        # newest-first, like the Delta ORDER BY run_at DESC
        return [dict(r) for r in sorted(data, key=lambda r: r.get("runAt") or "", reverse=True)[:n]]

    return {"append": append, "recent": recent, "_data": data}


def _schema():
    try:
        from pyspark.sql.types import (
            StructType, StructField, StringType, DoubleType, IntegerType, BooleanType,
        )
        t = {"peak_cu_pct": DoubleType(), "throttle_minutes": DoubleType(),
             "item_count": IntegerType(), "collector_ok": BooleanType()}
        return StructType([
            StructField(col, t.get(col, StringType()), True) for _, col in _FIELDS
        ])
    except ImportError:
        return None


def create_readings_store_delta(catalog, schema, *, spark=None):
    """Delta-backed append-only store on ``tier2_readings``. Use in production."""
    table = f"`{catalog}`.`{schema}`.tier2_readings"

    def _get_spark():
        nonlocal spark
        if spark is not None:
            return spark
        from pyspark.sql import SparkSession
        spark = SparkSession.getActiveSession()
        if spark is None:
            raise RuntimeError("No active SparkSession")
        return spark

    def append(reading):
        s = _get_spark()
        s.createDataFrame([_to_row(reading)], schema=_schema()).write.mode("append").saveAsTable(table)

    def recent(n=12):
        s = _get_spark()
        rows = s.sql(f"SELECT * FROM {table} ORDER BY run_at DESC LIMIT {int(n)}").collect()
        return [_from_row(r.asDict()) for r in rows]

    return {"append": append, "recent": recent}
