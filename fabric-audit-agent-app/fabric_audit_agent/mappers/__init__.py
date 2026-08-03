"""Map a raw API bundle into the complete facts shape. Port of ``core/mappers/index.js``. Pure."""
import math
from .capacity import map_capacity


def _d(v, default):
    """nullish default: v if not None else default (mirrors JS ??)."""
    return v if v is not None else default


def _round1(x):
    return math.floor(x * 10 + 0.5) / 10


def _to_gb(bytes_):
    return _round1((bytes_ if bytes_ is not None else 0) / 1e9)


def map_models(raw=None):
    raw = raw or []
    return [{
        "workspace": m.get("groupName"), "name": m.get("name"),
        "sizeGB": _to_gb(m.get("sizeBytes")), "bidirectionalRels": _d(m.get("relationshipsBidi"), 0),
        "autoDateTime": bool(m.get("autoTimeIntelligence")), "refreshFailRatePct": _d(m.get("refreshFailureRatePct"), 0),
        "observedAt": _d(m.get("observedAt"), ""),
    } for m in raw]


def map_reports(raw=None):
    raw = raw or []
    return [{
        "workspace": r.get("groupName"), "name": r.get("name"),
        "visuals": _d(r.get("visualCount"), 0), "mode": _d(r.get("storageMode"), "Import"),
        "slowestVisualMs": _d(r.get("slowestVisualMs"), 0), "source": _d(r.get("datasourceType"), "unknown"),
    } for r in raw]


def map_pipelines(raw=None):
    raw = raw or []
    return [{
        "workspace": p.get("groupName"), "name": p.get("name"),
        "lastStatus": _d(p.get("lastRunStatus"), "Succeeded"), "failRatePct": _d(p.get("failurePct"), 0),
        "gatewayHealthy": p.get("gatewayHealthy") is not False, "lastRunAt": _d(p.get("lastRunTime"), ""),
    } for p in raw]


def map_lineage(raw=None):
    raw = raw or {}
    return {
        "nodes": [{"id": i.get("id"), "type": i.get("itemType"), "workspace": i.get("groupName"), "name": i.get("displayName"), "status": _d(i.get("status"), "OK"), "failedAt": i.get("failedAt")} for i in (raw.get("items") or [])],
        "edges": [{"from": l.get("source"), "to": l.get("target")} for l in (raw.get("links") or [])],
    }


def map_access(raw=None):
    raw = raw or {}
    return {
        "adminGrants": _d(raw.get("adminGrants"), []),
        "externalShares": _d(raw.get("externalShares"), []),
        "accessEvents": _d(raw.get("accessEvents"), []),
    }


def map_usage(raw=None):
    raw = raw or {}
    return {
        "reports": [{"workspace": r.get("groupName"), "name": r.get("name"), "views30d": _d(r.get("views30d"), 0)} for r in (raw.get("reportViews") or [])],
        "capacities": [{"id": c.get("id"), "sku": c.get("sku"), "avgCuPct": _d(c.get("avgCuPercent"), 0)} for c in (raw.get("capacityUtil") or [])],
    }


def to_facts(raw=None):
    raw = raw or {}
    return {
        **map_capacity({"capacity": raw.get("capacity"), "refreshes": raw.get("refreshes")}),
        "models": map_models(raw.get("datasets")),
        "reports": map_reports(raw.get("reports")),
        "pipelines": map_pipelines(raw.get("pipelines")),
        "lineage": map_lineage(raw.get("lineage")),
        "access": map_access(raw.get("access")),
        "usage": map_usage(raw.get("usage")),
    }
