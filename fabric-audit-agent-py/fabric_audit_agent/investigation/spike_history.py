"""Per-user spike history: every high-consumption event + counts + time-of-day + workload split.
Pure / stdlib — input is already-normalized event dicts (from events.normalize_event).
A 'spike' is an event above the user's own p95 OR above an absolute floor (floor_cu)."""

from .baseline import compute_baseline
from .events import is_spike


def user_spike_history(events, user, *, floor_cu=0):
    """Return spike history for *user* derived from *events* (normalized event dicts).

    Args:
        events:    Iterable of normalized event dicts
                   ({ts,user,item,workspace,operation,kind,cuSeconds,durationMs,throttled}).
        user:      Email string to filter on (case-sensitive — normalize before calling).
        floor_cu:  Absolute CU-seconds floor; an event >= this is always a spike (default 0).

    Returns dict:
        {
            user:                 str,
            spikeCount:           int,
            totalCuSeconds:       float,
            peakCuSeconds:        float,
            spikes:               [{ts, item, operation, kind, cuSeconds}, ...] sorted cu desc,
            topItems:             [{item, cuSeconds}, ...] sorted cu desc,
            byHour:               {hour_int: spike_event_count},
            interactiveVsRefresh: {interactiveCuSeconds, refreshCuSeconds},
        }
    """
    user_events = [e for e in events if e.get("user") == user]

    if not user_events:
        return {
            "user": user,
            "spikeCount": 0,
            "totalCuSeconds": 0,
            "peakCuSeconds": 0,
            "spikes": [],
            "topItems": [],
            "byHour": {},
            "interactiveVsRefresh": {"interactiveCuSeconds": 0.0, "refreshCuSeconds": 0.0},
        }

    baseline = compute_baseline(user_events)
    p95 = baseline.get("p95")

    # Treat floor_cu=0 (falsy) as "no absolute floor" — pass None so is_spike only uses p95.
    effective_floor = floor_cu if floor_cu else None

    # Identify spike events
    spike_events = [
        e for e in user_events
        if is_spike(e, p95=p95, floor_cu=effective_floor)
    ]
    spike_events_sorted = sorted(spike_events, key=lambda e: e.get("cuSeconds", 0), reverse=True)

    # totalCuSeconds and peakCuSeconds over ALL user events (not just spikes)
    total_cu = sum(e.get("cuSeconds", 0) for e in user_events)
    peak_cu = max((e.get("cuSeconds", 0) for e in user_events), default=0)

    # spikes list — only the needed fields, sorted by cuSeconds desc
    spikes = [
        {
            "ts": e.get("ts", ""),
            "item": e.get("item"),
            "operation": e.get("operation", ""),
            "kind": e.get("kind", ""),
            "cuSeconds": e.get("cuSeconds", 0),
        }
        for e in spike_events_sorted
    ]

    # topItems — sum cuSeconds per item across ALL user events, sorted desc
    item_totals = {}
    for e in user_events:
        item = e.get("item") or ""
        item_totals[item] = item_totals.get(item, 0.0) + (e.get("cuSeconds", 0) or 0)
    top_items = sorted(
        [{"item": k, "cuSeconds": v} for k, v in item_totals.items()],
        key=lambda x: x["cuSeconds"],
        reverse=True,
    )

    # byHour — spike event count by UTC hour (parsed from ts ISO string)
    by_hour = {}
    for e in spike_events:
        ts = e.get("ts", "")
        hour = _parse_hour(ts)
        if hour is not None:
            by_hour[hour] = by_hour.get(hour, 0) + 1

    # interactiveVsRefresh — CU totals by kind across ALL user events
    interactive_cu = sum(
        e.get("cuSeconds", 0) for e in user_events if e.get("kind") == "interactive"
    )
    refresh_cu = sum(
        e.get("cuSeconds", 0) for e in user_events if e.get("kind") == "refresh"
    )

    return {
        "user": user,
        "spikeCount": len(spikes),
        "totalCuSeconds": total_cu,
        "peakCuSeconds": peak_cu,
        "spikes": spikes,
        "topItems": top_items,
        "byHour": by_hour,
        "interactiveVsRefresh": {
            "interactiveCuSeconds": interactive_cu,
            "refreshCuSeconds": refresh_cu,
        },
    }


def _parse_hour(ts):
    """Extract UTC hour integer from an ISO-8601 timestamp string, or return None."""
    if not ts:
        return None
    # Handles '2026-06-30T15:40:00Z' and '2026-06-30T15:40Z'
    try:
        t_part = ts.split("T", 1)[1] if "T" in ts else ""
        if not t_part:
            return None
        hour_str = t_part.split(":")[0]
        return int(hour_str)
    except (IndexError, ValueError):
        return None
