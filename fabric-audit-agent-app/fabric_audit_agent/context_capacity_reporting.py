"""Capacity reporting store — the ``capacity_reporting`` Delta table (B4, Design A' Phase B).

Long-tail analytical archive of what each 5-minute tier2 sweep saw: peak CU%, throttle
minutes, overage state, Fabric threshold pcts, and which tier2 checks fired that run.
Separate from ``tier2_readings`` (context_readings.py) which is a small rolling window used
only by the stateful gates — this store is meant for weekly / monthly trend reports and
post-incident review ("what did capacity look like leading up to this?").

Append-only. Rows are never mutated after write. Older rows can be pruned by a retention job
(not in scope for B4). Store contract: ``{"append", "recent", "query_range"}``.

Reading dicts are camelCase (project convention); Delta columns are snake_case — mapped by
``_to_row`` / ``_from_row``. ``signalTypes`` is a list on the dict, serialized to a compact
JSON string on the Delta side (STRING column) so an analyst can filter / explode with
``from_json`` without needing an array column.
"""
import json


_FIELDS = [
    ("runAt", "run_at"),
    ("capacityId", "capacity_id"),
    ("peakCuPct", "peak_cu_pct"),
    ("peakAt", "peak_at"),
    ("throttleMinutes", "throttle_minutes"),
    ("overageTotalMs", "overage_total_ms"),
    ("overageCumulativePct", "overage_cumulative_pct"),
    ("minutesToBurndown", "minutes_to_burndown"),
    ("maxInteractiveDelayPct", "max_interactive_delay_pct"),
    ("maxInteractiveRejectionPct", "max_interactive_rejection_pct"),
    ("maxBackgroundRejectionPct", "max_background_rejection_pct"),
    ("itemCount", "item_count"),
    ("signalTypes", "signal_types"),        # JSON-encoded list of check names that fired
    ("collectorOk", "collector_ok"),
]


def _to_row(reading):
    """camelCase reading dict -> snake_case Delta row. ``signalTypes`` is JSON-encoded."""
    row = {col: reading.get(cc) for cc, col in _FIELDS}
    sigs = row.get("signal_types")
    if sigs is not None and not isinstance(sigs, str):
        # List / tuple / None-safe: dump to compact JSON so a downstream ``from_json`` reads
        # a proper array. An empty list is serialized as ``"[]"`` (still valid) so consumers
        # can distinguish "sweep ran, nothing fired" from "no row here at all" (NULL).
        row["signal_types"] = json.dumps(list(sigs), separators=(",", ":"))
    return row


def _from_row(row):
    """snake_case Delta row -> camelCase reading dict. ``signalTypes`` is JSON-decoded."""
    out = {cc: row.get(col) for cc, col in _FIELDS}
    sigs = out.get("signalTypes")
    if isinstance(sigs, str):
        try:
            out["signalTypes"] = json.loads(sigs)
        except (TypeError, ValueError):
            out["signalTypes"] = []
    return out


def _extract_from_facts(facts, *, run_at, signal_types=None, collector_ok=True):
    """Build a reading row from a tier2 facts dict. Pure — no I/O. Called by run_tier2_check
    at append time, and by tests to build fixtures without duplicating field names.

    ``signal_types`` is the list of check names that fired this run (e.g.
    ``["throttle", "pressure"]``). Empty list means "sweep succeeded, no tier2 check fired."
    ``None`` is a legitimate "we didn't compute this yet" marker — a downstream analyst can
    tell the difference from the JSON payload.
    """
    cap = (facts or {}).get("capacity") or {}
    items = (facts or {}).get("items") or []
    return {
        "runAt": run_at,
        "capacityId": cap.get("capacityId"),
        "peakCuPct": cap.get("peakCuPct"),
        "peakAt": cap.get("peakAt"),
        "throttleMinutes": cap.get("throttleMinutes"),
        "overageTotalMs": cap.get("overageTotalMs"),
        "overageCumulativePct": cap.get("overageCumulativePct"),
        "minutesToBurndown": cap.get("minutesToBurndown"),
        "maxInteractiveDelayPct": cap.get("maxInteractiveDelayPct"),
        "maxInteractiveRejectionPct": cap.get("maxInteractiveRejectionPct"),
        "maxBackgroundRejectionPct": cap.get("maxBackgroundRejectionPct"),
        "itemCount": len(items),
        "signalTypes": list(signal_types) if signal_types is not None else None,
        "collectorOk": bool(collector_ok),
    }


def create_capacity_reporting_store_memory(initial=None):
    """In-memory append-only store. Tests/offline only — no cross-run persistence."""
    data = [dict(r) for r in (initial or [])]

    def append(reading):
        data.append(dict(reading))

    def recent(n=100):
        """Newest-first snapshot of the last ``n`` rows. Mirrors the Delta ORDER BY DESC LIMIT."""
        return [dict(r) for r in sorted(data, key=lambda r: r.get("runAt") or "",
                                        reverse=True)[:n]]

    def query_range(start, end):
        """All rows whose ``runAt`` falls in the closed interval [start, end] (ISO strings,
        lexicographic compare — ISO-8601 sorts correctly as strings). Newest-first."""
        rows = [r for r in data if (r.get("runAt") or "") >= start
                and (r.get("runAt") or "") <= end]
        return [dict(r) for r in sorted(rows, key=lambda r: r.get("runAt") or "",
                                        reverse=True)]

    return {"append": append, "recent": recent, "query_range": query_range, "_data": data}


def _schema():
    try:
        from pyspark.sql.types import (
            StructType, StructField, StringType, DoubleType, LongType, IntegerType,
            BooleanType,
        )
        t = {"peak_cu_pct": DoubleType(), "throttle_minutes": DoubleType(),
             "overage_total_ms": DoubleType(), "overage_cumulative_pct": DoubleType(),
             "minutes_to_burndown": DoubleType(),
             "max_interactive_delay_pct": DoubleType(),
             "max_interactive_rejection_pct": DoubleType(),
             "max_background_rejection_pct": DoubleType(),
             "item_count": IntegerType(), "collector_ok": BooleanType()}
        return StructType([
            StructField(col, t.get(col, StringType()), True) for _, col in _FIELDS
        ])
    except ImportError:
        return None


def create_capacity_reporting_store_delta(catalog, schema, *, spark=None):
    """Delta-backed append-only store on ``capacity_reporting``. Use in production."""
    table = f"`{catalog}`.`{schema}`.capacity_reporting"

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
        (s.createDataFrame([_to_row(reading)], schema=_schema())
             .write.mode("append").saveAsTable(table))

    def recent(n=100):
        s = _get_spark()
        rows = s.sql(f"SELECT * FROM {table} ORDER BY run_at DESC LIMIT {int(n)}").collect()
        return [_from_row(r.asDict()) for r in rows]

    def query_range(start, end):
        s = _get_spark()
        # Parameterized string literals — start / end are ISO-8601 timestamps composed by
        # the caller (never user input at this layer), so f-string interpolation is safe.
        rows = s.sql(
            f"SELECT * FROM {table} WHERE run_at >= '{start}' AND run_at <= '{end}' "
            "ORDER BY run_at DESC"
        ).collect()
        return [_from_row(r.asDict()) for r in rows]

    return {"append": append, "recent": recent, "query_range": query_range}
