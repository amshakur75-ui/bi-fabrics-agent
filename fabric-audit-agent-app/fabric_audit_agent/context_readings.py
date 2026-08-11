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
    # Tracked SEPARATELY from collectorOk. collectorOk is an OR across sources, so if the capacity
    # source (Eventhouse) returns zero rows without raising while Log Analytics attribution keeps
    # flowing, collectorOk stays True -- and every capacity gate then reasons from nothing while the
    # blindness detector sees a healthy collector. Since peakCuPct drives sustained/rate_change and
    # every capacity threshold, "did we get a capacity reading?" needs its own answer.
    ("capacityOk", "capacity_ok"),
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
        # newest-first, like the Delta ORDER BY run_at DESC.
        #
        # The insertion index is a TIE-BREAK, not decoration. `sorted(..., reverse=True)` is stable,
        # so rows sharing a runAt came back in INSERTION order -- i.e. oldest-first, the exact inverse
        # of this function's contract -- and _check_silent_failure reads `readings[:n]` believing the
        # first element is the newest. Equal timestamps are not exotic: `datetime.now()` has ~15ms
        # granularity on Windows, so any two appends in the same tick collide, and the blindness
        # alarm then could not clear after the collector recovered.
        indexed = list(enumerate(data))
        indexed.sort(key=lambda pair: (pair[1].get("runAt") or "", pair[0]), reverse=True)
        return [dict(r) for _i, r in indexed[:n]]

    return {"append": append, "recent": recent, "_data": data}


def _schema():
    try:
        from pyspark.sql.types import (
            StructType, StructField, StringType, DoubleType, IntegerType, BooleanType,
        )
        t = {"peak_cu_pct": DoubleType(), "throttle_minutes": DoubleType(),
             "item_count": IntegerType(), "collector_ok": BooleanType(),
             "capacity_ok": BooleanType()}
        return StructType([
            StructField(col, t.get(col, StringType()), True) for _, col in _FIELDS
        ])
    except ImportError:
        return None


def create_readings_store_delta(catalog, schema, *, spark=None):
    """Delta-backed append-only store on ``tier2_readings``. Use in production."""
    table = f"`{catalog}`.`{schema}`.tier2_readings"

    _ensured = {"done": False}
    _COL_SQL_TYPE = {"peak_cu_pct": "DOUBLE", "throttle_minutes": "DOUBLE",
                     "item_count": "INT", "collector_ok": "BOOLEAN",
                     "capacity_ok": "BOOLEAN"}

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
        """Add any missing ``_FIELDS`` column to the table (once per store), like the alerts and
        capacity_reporting stores already do.

        This store was the ONE of the four without it, and it is the worst place to lack it: a
        schema drift makes every ``append`` fail, ``_record_reading`` returns [], and then ALL THREE
        stateful gates go quiet -- sustained, rate_change, and ``_check_silent_failure``, which is
        the BLINDNESS DETECTOR. So the one gate whose job is to notice the agent has stopped seeing
        would be taken out by the same fault it exists to report. Never fatal: a failure here just
        leaves the column absent and the append error surfaces through the caller's health record.
        """
        if _ensured["done"]:
            return
        try:
            existing = {r["col_name"] for r in s.sql(f"DESCRIBE TABLE {table}").collect()}
            for _, col in _FIELDS:
                if col not in existing:
                    s.sql(f"ALTER TABLE {table} ADD COLUMNS ({col} {_COL_SQL_TYPE.get(col, 'STRING')})")
        except Exception as exc:
            print(f"[readings] schema self-heal skipped ({type(exc).__name__}: {exc})")
        _ensured["done"] = True

    def append(reading):
        s = _get_spark()
        _ensure_schema(s)
        s.createDataFrame([_to_row(reading)], schema=_schema()).write.mode("append").saveAsTable(table)

    def recent(n=12):
        s = _get_spark()
        _ensure_schema(s)
        rows = s.sql(f"SELECT * FROM {table} ORDER BY run_at DESC LIMIT {int(n)}").collect()
        return [_from_row(r.asDict()) for r in rows]

    return {"append": append, "recent": recent}
