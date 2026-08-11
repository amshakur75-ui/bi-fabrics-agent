"""Phase 6 — query recent audit_findings as pre-investigation context.

Before starting an investigation on a given capacity/item/user, this module queries
the last N rows from ``audit_findings`` (Phase 5 Delta table) for that scope and
formats them as plain-language labeled context for the system prompt / tool loop.

A missing or empty result never blocks the investigation — this is enrichment only.
Prior findings are CONTEXT, never a conclusion — the agent still gathers fresh evidence.
"""


def query_recent_findings(store, *, scope=None, tenant=None, limit=5):
    """Return recent findings matching *scope* as a list of dicts.

    ``store`` is a findings-store with a ``{"query": fn(scope, tenant, limit) -> list}``
    interface. If store is None or the query fails, returns an empty list (never blocks).

    Each returned dict has at minimum: ``findingKey``, ``level``, ``whatText``, ``runAt``.
    """
    if store is None:
        return []
    query_fn = store.get("query")
    if query_fn is None:
        return []
    try:
        return query_fn(scope=scope, tenant=tenant, limit=limit) or []
    except Exception:
        return []


def format_context(findings, scope=None):
    """Format recent findings as plain-language context for injection into the agent's loop.

    Returns a string suitable for prepending to an investigation prompt, or empty string
    if there are no findings to report.
    """
    if not findings:
        return ""
    scope_label = f" for {scope}" if scope else ""
    count = len(findings)
    lines = [f"**{count} prior finding{'s' if count != 1 else ''}{scope_label} in recent runs:**"]
    for f in findings:
        level = f.get("level", "Info")
        what = f.get("whatText") or f.get("what") or "(no description)"
        run_at = f.get("runAt") or "unknown date"
        lines.append(f"- [{level}] {what} (seen {run_at})")
    lines.append("")
    lines.append("_Prior findings are context only — gather fresh evidence before concluding._")
    return "\n".join(lines)


def _get_findings_schema():
    try:
        from pyspark.sql.types import StructType, StructField, StringType, BooleanType
        return StructType([
            StructField("run_at", StringType(), True),
            StructField("tenant", StringType(), True),
            StructField("finding_key", StringType(), True),
            StructField("level", StringType(), True),
            StructField("finding_type", StringType(), True),
            StructField("resource", StringType(), True),
            StructField("what_text", StringType(), True),
            StructField("confidence", StringType(), True),
            StructField("suppressed", BooleanType(), True),
        ])
    except ImportError:
        return None


def create_findings_store_delta(catalog, schema, *, spark=None):
    """Create a findings store backed by the ``audit_findings`` Delta table.

    Returns ``{"query": fn, "write": fn}`` — the query side for Phase 6, the write
    side for pipeline.py to persist individual findings after each run.
    """
    table = f"`{catalog}`.`{schema}`.audit_findings"

    def _get_spark():
        nonlocal spark
        if spark is not None:
            return spark
        from pyspark.sql import SparkSession
        spark = SparkSession.getActiveSession()
        if spark is None:
            raise RuntimeError("No active SparkSession")
        return spark

    def query(*, scope=None, tenant=None, limit=5):
        s = _get_spark()
        query_str = f"SELECT * FROM {table}"
        conditions = []
        if scope:
            safe_scope = str(scope).replace("'", "''")
            conditions.append(f"resource = '{safe_scope}'")
        if tenant:
            safe_tenant = str(tenant).replace("'", "''")
            conditions.append(f"tenant = '{safe_tenant}'")
        conditions.append("suppressed = false")
        if conditions:
            query_str += " WHERE " + " AND ".join(conditions)
        query_str += " ORDER BY run_at DESC"
        query_str += f" LIMIT {int(limit)}"
        rows = s.sql(query_str).collect()
        return [
            {
                "findingKey": r["finding_key"],
                "level": r["level"],
                "whatText": r["what_text"],
                "runAt": r["run_at"],
                "resource": r["resource"],
                "confidence": r["confidence"],
            }
            for r in rows
        ]

    def write(run_at, tenant, findings):
        s = _get_spark()
        if not findings:
            return
        rows = []
        for f in findings:
            rows.append({
                "run_at": run_at,
                "tenant": tenant,
                "finding_key": f.get("key"),
                "level": (f.get("score") or {}).get("level"),
                # Derive from the key when absent. create_finding() returns EXACTLY its seven
                # canonical fields (what/where/when/why/impact/fix/score) and DROPS `type`, so this
                # column has been NULL on every row ever written -- 7,344 of 7,344 in production.
                # `key` survives (the pipeline re-attaches it), which is why recurrence, keyed on
                # finding_key, still works and this stayed invisible. Nothing reads the column
                # today, so this is a dead-payload fix, not a behaviour change: it makes
                # "SELECT ... GROUP BY finding_type" answer the question it appears to answer.
                "finding_type": f.get("type") or (str(f.get("key") or "").split("::")[0] or None),
                "resource": f.get("resource") or f.get("where"),
                "what_text": f.get("what"),
                "confidence": f.get("confidence"),
                "suppressed": bool(f.get("suppressed")),
            })
        schema = _get_findings_schema()
        df = s.createDataFrame(rows, schema=schema) if schema else s.createDataFrame(rows)
        df.write.mode("append").saveAsTable(table)

    return {"query": query, "write": write}
