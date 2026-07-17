"""Normalized capacity event + spike definition (the metric layer for 'a spike'). Pure/stdlib.
One event shape so all Phase-3 analysis is source-agnostic + offline-testable."""
_REFRESH_OPS = {"CommandEnd", "ProgressReportEnd", "Refresh", "CommandBegin"}

def _identity_email(row):
    ident = row.get("Identity")
    if isinstance(ident, dict):
        return ident.get("Email") or ident.get("email")
    return row.get("ExecutingUser") or row.get("user") or row.get("User")

def normalize_event(row):
    cpu = row.get("CpuTimeMs")
    dur = row.get("DurationMs")
    ms = cpu if cpu is not None else dur
    op = row.get("OperationName") or row.get("operation") or ""
    op_detail = row.get("OperationDetailName") or row.get("operationDetail")
    raw_text = row.get("EventText") if row.get("EventText") is not None else row.get("queryText")
    return {
        "ts": row.get("TimeGenerated") or row.get("Timestamp") or row.get("ts") or "",
        "user": (_identity_email(row) or "").lower() or None,
        "item": row.get("ArtifactName") or row.get("ItemName") or row.get("item"),
        "workspace": row.get("PowerBIWorkspaceName") or row.get("WorkspaceName") or row.get("workspace"),
        "operation": op,
        "operationDetail": op_detail,   # MdxQuery / DaxQuery / Restore / ... (distinguishes MDX vs DAX vs admin)
        "kind": "refresh" if op in _REFRESH_OPS else "interactive",
        "cuSeconds": round((ms or 0) / 1000.0, 3),
        "durationMs": dur,
        "throttled": bool(row.get("throttled")),
        "queryText": raw_text,
    }

def is_spike(event, *, p95, floor_cu):
    cu = event.get("cuSeconds") or 0
    return (p95 is not None and cu > p95) or (floor_cu is not None and cu >= floor_cu)
