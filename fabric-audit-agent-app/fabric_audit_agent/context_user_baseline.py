"""User baseline store — the ``user_baseline`` Delta table (per-user p95 + estate-wide fallback).

Written NIGHTLY by ``automation.user_baseline_bootstrap.run_bootstrap`` from 14 days of Log
Analytics activity; read every 5-min sweep by B2's ``detect_user_baseline_deviation_precomputed``.
The store exposes ``{"get_user", "get_estate", "upsert_many"}``.

Design A' 3-layer fallback (personalized / estate-wide / silent):
  - ``get_user(user)``  returns that user's personalized baseline if their row exists.
  - ``get_estate()``    returns the estate-wide baseline (single row) for cold-start users
                        who don't yet have ``min_history`` operations of their own.
  - Neither present → the detector stays silent (no false alerts before we have history).

Baseline dicts are camelCase (project convention); Delta columns are snake_case — mapped by
``_to_row`` / ``_from_row``.
"""

_FIELDS = [
    ("scope", "scope"),
    ("user", "user_id"),
    ("p50", "p50"),
    ("p95", "p95"),
    ("count", "sample_count"),
    ("min", "min_cu_seconds"),
    ("max", "max_cu_seconds"),
    ("asOf", "as_of"),
]


def _to_row(baseline):
    """camelCase baseline dict -> snake_case Delta row."""
    return {col: baseline.get(cc) for cc, col in _FIELDS}


def _from_row(row):
    """snake_case Delta row -> camelCase baseline dict."""
    return {cc: row.get(col) for cc, col in _FIELDS}


def _dedupe_rows(rows):
    """Collapse duplicate (scope, user) rows, keeping the best-sampled one.

    The Delta MERGE matches on ``(scope, user_id)`` and Delta REJECTS a source frame containing
    two rows that match the same target row ("multiple source rows matched..."), so a duplicate
    took the entire nightly job down — and ``upsert_many`` has no try/except, so every baseline
    went stale silently. Duplicates are reachable: the aggregate KQL groups with
    ``summarize ... by _euser``, which is CASE-SENSITIVE in Kusto, and then ``tolower()``
    collapses "A.User@x" and "a.user@x" into one key. Keep the row with the larger sample count.
    """
    best = {}
    for r in rows or []:
        key = (r.get("scope"), (r.get("user") or "").lower())
        cur = best.get(key)
        if cur is None or (r.get("count") or 0) > (cur.get("count") or 0):
            best[key] = r
    return list(best.values())


def create_user_baseline_store_memory(initial=None):
    """In-memory store keyed by (scope, user). Tests + offline only — no cross-run persistence.

    ``initial`` (optional): a list of baseline row dicts to seed the store with, matching the
    shape produced by ``user_baseline_bootstrap.build_baselines``.
    """
    def _key(row):
        return (row["scope"], row.get("user") or "")
    data = {}
    for r in initial or []:
        data[_key(r)] = dict(r)

    def get_user(user):
        return dict(data[("user", user)]) if ("user", user) in data else None

    def get_estate():
        return dict(data[("estate", "")]) if ("estate", "") in data else None

    def get_all_users():
        return {k[1]: dict(v) for k, v in data.items() if k[0] == "user"}

    def upsert_many(rows):
        # Same de-dupe as the Delta store, so the double cannot hide a duplicate-row bug.
        for r in _dedupe_rows(rows):
            data[_key(r)] = dict(r)

    def all_rows():
        return [dict(r) for r in data.values()]

    return {"get_user": get_user, "get_estate": get_estate,
            "get_all_users": get_all_users,
            "upsert_many": upsert_many, "all": all_rows, "_data": data}


def _schema():
    try:
        from pyspark.sql.types import (
            StructType, StructField, StringType, DoubleType, LongType,
        )
        t = {"p50": DoubleType(), "p95": DoubleType(), "sample_count": LongType(),
             "min_cu_seconds": DoubleType(), "max_cu_seconds": DoubleType()}
        return StructType([
            StructField(col, t.get(col, StringType()), True) for _, col in _FIELDS
        ])
    except ImportError:
        return None


_CREATE_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS {table} ("
    "scope STRING, user_id STRING, p50 DOUBLE, p95 DOUBLE, "
    "sample_count BIGINT, min_cu_seconds DOUBLE, max_cu_seconds DOUBLE, "
    "as_of STRING"
    ") USING DELTA "
    "TBLPROPERTIES ("
    "'delta.autoOptimize.optimizeWrite' = 'true', "
    "'delta.autoOptimize.autoCompact' = 'true'"
    ")"
)


def create_user_baseline_store_delta(catalog, schema, *, spark=None):
    """Delta-backed store on ``user_baseline`` (Spark MERGE upsert). Use in production.

    Table shape is defined by ``scripts/create_user_baseline_delta.sql`` — kept as the
    canonical spec for schema review, but the store itself also does an idempotent
    ``CREATE TABLE IF NOT EXISTS`` on first write so the bootstrap job succeeds even when
    the table hasn't been pre-provisioned. Same pattern as ``context_alerts._ensure_schema``.
    """
    table = f"`{catalog}`.`{schema}`.user_baseline"
    _ensured = {"done": False}

    def _get_spark():
        nonlocal spark
        if spark is not None:
            return spark
        from pyspark.sql import SparkSession
        spark = SparkSession.getActiveSession()
        if spark is None:
            raise RuntimeError("No active SparkSession")
        return spark

    def _ensure_table(s):
        if _ensured["done"]:
            return
        try:
            s.sql(_CREATE_TABLE_SQL.format(table=table))
        except Exception as exc:
            print(f"[user_baseline] table ensure skipped ({type(exc).__name__}: {exc})")
        _ensured["done"] = True

    def get_user(user):
        s = _get_spark()
        _ensure_table(s)
        # Escape single quotes: `user` comes from Log Analytics ExecutingUser /
        # EffectiveUsername, and XMLA / Analyze-in-Excel sessions routinely carry a DISPLAY
        # name rather than a UPN (e.g. "O'Brien, Sean"). An unescaped apostrophe produced a
        # malformed query whose exception the detector swallowed, silently demoting that user
        # to the estate baseline forever. Prefer get_all_users() in hot paths.
        safe = str(user).replace("'", "''")
        rows = s.sql(
            f"SELECT * FROM {table} WHERE scope = 'user' AND user_id = '{safe}'"
        ).collect()
        return _from_row(rows[0].asDict()) if rows else None

    def get_estate():
        s = _get_spark()
        _ensure_table(s)
        rows = s.sql(f"SELECT * FROM {table} WHERE scope = 'estate' LIMIT 1").collect()
        return _from_row(rows[0].asDict()) if rows else None

    def get_all_users():
        """Load EVERY per-user baseline in ONE query, keyed by user id.

        The per-event ``get_user`` path issued one Spark query + driver collect PER EVENT. At a
        few thousand events per 5-minute sweep that is thousands of round-trips — minutes of
        wall time inside a job scheduled every 5 minutes, causing overlapping runs and
        heartbeat gaps. The table is one row per active user (thousands at most), so a single
        load is strictly cheaper and takes no user input (no injection seam)."""
        s = _get_spark()
        _ensure_table(s)
        rows = s.sql(f"SELECT * FROM {table} WHERE scope = 'user'").collect()
        out = {}
        for r in rows:
            d = r.asDict()
            uid = d.get("user_id")
            if uid:
                out[uid] = _from_row(d)
        return out

    def upsert_many(rows):
        rows = _dedupe_rows(rows)
        if not rows:
            return
        s = _get_spark()
        _ensure_table(s)
        try:
            s.sql("SET spark.databricks.delta.schema.autoMerge.enabled = true")
        except Exception:
            pass
        df = s.createDataFrame([_to_row(r) for r in rows], schema=_schema())
        df.createOrReplaceTempView("_user_baseline_upsert")
        # Composite key: (scope, user_id). estate rows have user_id=NULL so the ON clause
        # falls back to matching by scope alone for that case; NULL-safe eq (<=>) handles it.
        s.sql(
            f"MERGE INTO {table} t USING _user_baseline_upsert src "
            "ON t.scope = src.scope AND t.user_id <=> src.user_id "
            "WHEN MATCHED THEN UPDATE SET * "
            "WHEN NOT MATCHED THEN INSERT *"
        )

    return {"get_user": get_user, "get_estate": get_estate,
            "get_all_users": get_all_users, "upsert_many": upsert_many}
