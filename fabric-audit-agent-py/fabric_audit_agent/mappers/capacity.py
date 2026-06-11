"""Map raw Power BI/Fabric capacity telemetry into the ``capacity`` facts shape.

Port of ``core/mappers/capacity.js``. Pure: raw API JSON in, facts out.
"""
import math
from datetime import datetime, timezone


def _parse_ms(s):
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000


def _round1(x):
    return math.floor(x * 10 + 0.5) / 10


def _duration_min(start_iso, end_iso):
    s, e = _parse_ms(start_iso), _parse_ms(end_iso)
    if s is not None and e is not None:
        return math.floor((e - s) / 60000 + 0.5)   # Math.round
    return 0


def map_capacity(raw=None):
    raw = raw or {}
    c = raw.get("capacity") or {}
    refreshes = [{
        "workspace": r.get("groupName"),
        "dataset": r.get("datasetName"),
        "scheduledAt": r.get("scheduleTime"),
        "durationMin": _duration_min(r.get("startTime"), r.get("endTime")),
        "sizeGB": _round1((r.get("sizeBytes") or 0) / 1e9),
    } for r in (raw.get("refreshes") or [])]
    return {
        "capacity": {
            "tenant": c.get("tenantName"),
            "capacityId": c.get("displayName") if c.get("displayName") is not None else c.get("id"),
            "sku": c.get("sku"),
            "memoryGB": c.get("memoryGb"),
            "peakCuPct": c.get("peakCuPercent"),
            "peakAt": c.get("peakTimestamp"),
            "throttleMinutes": c.get("throttledMinutes"),
            "refreshes": refreshes,
        }
    }
