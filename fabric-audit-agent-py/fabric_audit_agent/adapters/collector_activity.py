"""Activity / user-attribution collector — the WHO behind the 30% concentration alert.

NEW (no Node reference): builds on the ported attribution engine. Pulls per-user activity
for the audit window from the Power BI **Activity Events** admin API (and optionally Azure
**Log Analytics** for CU-cost-weighted ranking), groups events per item, and enriches
``facts["items"]`` so ``detectors.concentration`` can name **User -> Item -> Owner**.

The http client is injected (``get_json`` / ``post_json``) so it's testable offline and swaps
to ``adapters.clients.EntraHttp`` at deploy. Read-only.

Attribution sources (from the deployment research):
  - Activity Events (GetActivityEvents): ``UserId`` + ``Operation`` per item -> frequency
    ranking. Interactive ops (ViewReport, ...) name the consumer; background ops
    (RefreshDataset, ...) name the owner/initiator. No CU figure here.
  - Log Analytics (``ExecutingUser`` + ``CpuTimeMs``/``DurationMs``): CU-cost-weighted ranking.
No single source has CU + user together, so we correlate by item + time window.

Field names below are representative — verify exact casing against the live APIs at deploy.
"""
from ..attribution import enrich_items, DEFAULT_TOP_N

_ACTIVITY_URL = "https://api.powerbi.com/v1.0/myorg/admin/activityevents"

# Operations that are background/system work (the owner/initiator is named, not a consumer).
_BACKGROUND_OPS = {
    "refreshdataset", "refreshdataflow", "refreshdatamart", "ondemanddatasetrefresh",
    "scheduleddatasetrefresh", "refreshpipeline", "runpipeline", "executenotebook",
    "datamartrefresh", "refreshsqlendpoint",
}


def map_activity_event(entity):
    """Map a raw Activity Events entity to an attribution event. Pure.

    ``interactive`` is False for known background operations (case-insensitive), so a heavy
    refresh names the owner rather than an interactive consumer.
    """
    entity = entity or {}
    op = str(entity.get("Operation") or "").strip()
    item = (entity.get("ArtifactName") or entity.get("DatasetName")
            or entity.get("ReportName") or entity.get("DataflowName"))
    return {
        "user": entity.get("UserId") or entity.get("UserKey") or "",
        "item": item,
        "workspace": entity.get("WorkspaceName") or entity.get("WorkSpaceName"),
        "operation": op,
        "interactive": op.lower() not in _BACKGROUND_OPS,
        "time": entity.get("CreationTime"),
    }


def fetch_activity_events(http, start_iso, end_iso, base_url=None):
    """Page the Activity Events admin API over ``[start, end)`` and return mapped events.

    The API returns ``continuationUri`` until the window is exhausted (and requires the
    window to sit within a single UTC day). Guarded against runaway paging.
    """
    base = base_url or _ACTIVITY_URL
    url = f"{base}?startDateTime='{start_iso}'&endDateTime='{end_iso}'"
    events = []
    guard = 0
    while url and guard < 1000:
        guard += 1
        page = http.get_json(url)
        entities = (page.get("activityEventEntities") or []) if isinstance(page, dict) else []
        for ent in entities:
            events.append(map_activity_event(ent))
        url = page.get("continuationUri") if isinstance(page, dict) else None
    return events


def map_log_analytics_rows(resp):
    """Map an Azure Monitor / Log Analytics query response (tables/columns/rows) to cost
    events carrying ``cpuMs``/``durationMs`` (so attribution ranks by CU cost). Pure."""
    tables = (resp or {}).get("tables") or []
    if not tables:
        return []
    cols = [c.get("name") for c in (tables[0].get("columns") or [])]
    idx = {name: i for i, name in enumerate(cols)}

    def col(row, name):
        i = idx.get(name)
        return row[i] if (i is not None and i < len(row)) else None

    out = []
    for row in (tables[0].get("rows") or []):
        out.append({
            "user": col(row, "ExecutingUser") or col(row, "User") or "",
            "item": col(row, "ArtifactName") or col(row, "DatasetName") or col(row, "Item"),
            "cpuMs": col(row, "CpuTimeMs") or col(row, "cpuTimeMs"),
            "durationMs": col(row, "DurationMs") or col(row, "durationMs"),
            "interactive": True,   # LA query targets interactive ops; refine by Operation at deploy
        })
    return out


def fetch_log_analytics(http, workspace_id, kql, timespan=None):
    """Query Log Analytics for CU-cost events. Optional richer ranking source."""
    url = f"https://api.loganalytics.io/v1/workspaces/{workspace_id}/query"
    body = {"query": kql}
    if timespan:
        body["timespan"] = timespan
    return map_log_analytics_rows(http.post_json(url, body))


def group_events_by_item(events, log_events=None):
    """Group attribution events by item name (Activity Events + optional Log Analytics)."""
    by_item = {}
    for e in (events or []):
        item = e.get("item")
        if item:
            by_item.setdefault(item, []).append(e)
    for e in (log_events or []):
        item = e.get("item")
        if item:
            by_item.setdefault(item, []).append(e)
    return by_item


def create_activity_collector(http, config=None, base_collector=None, top_n=DEFAULT_TOP_N):
    """Wrap a base collector and enrich ``facts["items"]`` with user attribution.

    ``config`` keys: ``windowStart`` / ``windowEnd`` (ISO, same UTC day), ``activityUrl``
    (override), ``logAnalyticsEvents`` (pre-fetched LA cost events, optional). Items without
    activity are left untouched (so the detector falls back to "pending correlation").
    """
    config = config or {}

    def collect():
        facts = base_collector["collect"]() if base_collector else {}
        items = facts.get("items") or []
        if not items:
            return facts
        start, end = config.get("windowStart"), config.get("windowEnd")
        events = fetch_activity_events(http, start, end, config.get("activityUrl")) if (start and end) else []
        log_events = config.get("logAnalyticsEvents") or []
        by_item = group_events_by_item(events, log_events)
        return {**facts, "items": enrich_items(items, by_item, top_n=top_n)}

    return {"collect": collect}
