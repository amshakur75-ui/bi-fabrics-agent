"""Activity / user-attribution collector — the WHO behind the 30% concentration alert.

NEW (no Node reference): builds on the ported attribution engine. Pulls per-user activity
for the audit window from the Power BI **Activity Events** admin API (and optionally Azure
**Log Analytics** for CU-cost-weighted ranking), matches events to items by
**(workspace, name)**, and enriches ``facts["items"]`` so ``detectors.concentration`` can name
**User -> Item -> Owner**.

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
from ..attribution import attribute_users, DEFAULT_TOP_N

_ACTIVITY_URL = "https://api.powerbi.com/v1.0/myorg/admin/activityevents"

# Operations that are background/system work (the owner/initiator is named, not a consumer).
_BACKGROUND_OPS = {
    "refreshdataset", "refreshdataflow", "refreshdatamart", "ondemanddatasetrefresh",
    "scheduleddatasetrefresh", "refreshpipeline", "runpipeline", "executenotebook",
    "datamartrefresh", "refreshsqlendpoint",
}


def _first(*vals):
    """First value that ``is not None`` (so a real ``0`` cost survives, unlike ``a or b``)."""
    for v in vals:
        if v is not None:
            return v
    return None


def _is_background(operation):
    return bool(operation) and operation.lower() in _BACKGROUND_OPS


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
        "interactive": not _is_background(op),
        "time": entity.get("CreationTime"),
    }


def fetch_activity_events(http, start_iso, end_iso, base_url=None):
    """Page the Activity Events admin API over ``[start, end)`` and return mapped events.

    Follows ``continuationUri`` until exhausted; de-dupes seen URIs (so a server returning a
    self-referential link can't loop) and guards against runaway paging.
    """
    base = base_url or _ACTIVITY_URL
    url = f"{base}?startDateTime='{start_iso}'&endDateTime='{end_iso}'"
    events, seen = [], set()
    guard = 0
    while url and url not in seen and guard < 1000:
        seen.add(url)
        guard += 1
        page = http.get_json(url)
        entities = (page.get("activityEventEntities") or []) if isinstance(page, dict) else []
        for ent in entities:
            events.append(map_activity_event(ent))
        url = page.get("continuationUri") if isinstance(page, dict) else None
    return events


def map_log_analytics_rows(resp):
    """Map an Azure Monitor / Log Analytics query response (tables/columns/rows) to cost
    events carrying ``cpuMs``/``durationMs`` (so attribution ranks by CU cost). Pure.

    Uses first-non-None column lookups so a real ``0`` cost is preserved; classifies
    ``interactive`` from an ``Operation``/``OperationName`` column when present.
    """
    tables = (resp or {}).get("tables") or []
    if not tables:
        return []
    cols = [c.get("name") for c in (tables[0].get("columns") or [])]
    idx = {name: i for i, name in enumerate(cols)}

    def col(row, *names):
        for name in names:
            i = idx.get(name)
            if i is not None and i < len(row):
                v = row[i]
                if v is not None:
                    return v
        return None

    out = []
    for row in (tables[0].get("rows") or []):
        op = str(col(row, "OperationName", "Operation") or "").strip()
        out.append({
            "user": col(row, "ExecutingUser", "User") or "",
            "item": col(row, "ArtifactName", "DatasetName", "Item"),
            "workspace": col(row, "WorkspaceName", "Workspace"),
            "operation": op,
            "cpuMs": col(row, "CpuTimeMs", "cpuTimeMs"),
            "durationMs": col(row, "DurationMs", "durationMs"),
            # LA query may omit Operation; default to interactive only when it's truly unknown.
            "interactive": (not _is_background(op)) if op else True,
        })
    return out


def fetch_log_analytics(http, workspace_id, kql, timespan=None):
    """Query Log Analytics for CU-cost events. Optional richer ranking source."""
    url = f"https://api.loganalytics.io/v1/workspaces/{workspace_id}/query"
    body = {"query": kql}
    if timespan:
        body["timespan"] = timespan
    return map_log_analytics_rows(http.post_json(url, body))


def _events_for_item(item, events):
    """Events whose item name matches and whose workspace matches (or is unknown).

    Workspace-aware: two items with the same name in different workspaces don't cross-
    contaminate. Events with no workspace (e.g. Log Analytics rows) match by name only.
    """
    name, ws = item.get("name"), item.get("workspace")
    return [e for e in events if e.get("item") == name and e.get("workspace") in (None, ws)]


def create_activity_collector(http, config=None, base_collector=None, top_n=DEFAULT_TOP_N):
    """Wrap a base collector and enrich ``facts["items"]`` with user attribution.

    ``config`` keys: ``windowStart`` / ``windowEnd`` (ISO, same UTC day), ``activityUrl``
    (override), ``logAnalyticsEvents`` (pre-fetched LA cost events, optional). Items with no
    matching activity are left untouched (so the detector falls back to "pending correlation").
    """
    config = config or {}

    def collect():
        facts = base_collector["collect"]() if base_collector else {}
        items = facts.get("items") or []
        if not items:
            return facts
        start, end = config.get("windowStart"), config.get("windowEnd")
        events = fetch_activity_events(http, start, end, config.get("activityUrl")) if (start and end) else []
        all_events = [*events, *(config.get("logAnalyticsEvents") or [])]

        out = []
        for it in items:
            matched = _events_for_item(it, all_events)
            if not matched:
                out.append(it)
                continue
            a = attribute_users(matched, top_n=top_n, owner=it.get("owner"))
            out.append({
                **it,
                "topUsers": a["topUsers"],
                "userCount": a["userCount"],
                "background": a["background"],
                "owner": a["owner"] if a["owner"] is not None else it.get("owner"),
                "attributionMode": a["mode"],
            })
        return {**facts, "items": out}

    return {"collect": collect}
