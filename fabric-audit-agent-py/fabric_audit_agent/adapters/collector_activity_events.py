"""Tier-1 CollectorPort: Activity Events admin API → normalized-event-SHAPED records.

The graceful-degradation path (spec): when no Tier-2 per-query source (LA / Workspace
Monitoring) is configured, the event tools still get a real, timestamped, per-user operation
stream — ViewReport / RefreshDataset / ExecuteNotebook / ... — with ``cuSeconds=None`` and
``queryText=None`` carried HONESTLY (operation-level, no per-query cost; the envelope labels it).
Read-only; http injected (swaps to ``clients.EntraHttp`` at deploy).
"""
from .collector_activity import fetch_activity_events


def _to_event(a):
    return {
        "ts": a.get("time"),
        "user": a.get("user"),
        "item": a.get("item"),
        "workspace": a.get("workspace"),
        "kind": "interactive" if a.get("interactive") else "refresh",
        "cuSeconds": None,
        "queryText": None,
        "operation": a.get("operation"),
    }


def create_activity_event_collector(http, config=None):
    """``config``: ``start``/``end`` (ISO-8601, both required), optional ``user``/``item`` scope."""
    cfg = config or {}

    def collect():
        start, end = cfg.get("start"), cfg.get("end")
        if start is None or end is None:
            raise ValueError("activity event collector requires both 'start' and 'end' (ISO-8601)")
        events = [_to_event(a) for a in fetch_activity_events(http, start, end)]
        user = cfg.get("user")
        if user is not None:
            events = [e for e in events if (e.get("user") or "").lower() == str(user).lower()]
        item = cfg.get("item")
        if item is not None:
            events = [e for e in events if e.get("item") == item]
        return events

    return {"collect": collect}
