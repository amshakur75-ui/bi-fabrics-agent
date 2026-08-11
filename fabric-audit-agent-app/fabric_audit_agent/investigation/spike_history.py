"""Per-user spike history: every high-consumption event + counts + time-of-day + workload split.
Pure / stdlib — input is already-normalized event dicts (from events.normalize_event).
A 'spike' is an event above a MULTIPLE of the user's own p95 OR above an absolute floor
(floor_cu) — see the anomaly-test note in ``user_spike_history``."""

from ..config import DEFAULT_CONFIG
from .baseline import compute_baseline
from .events import is_spike

# Same multiple detectors/user_baseline.py applies, and for the same reason (see
# config.baselineSpikeMultiplier).
_SPIKE_MULTIPLIER = DEFAULT_CONFIG["activity"]["baselineSpikeMultiplier"]


def user_spike_history(events, user, *, floor_cu=0, multiplier=None):
    """Return spike history for *user* derived from *events* (normalized event dicts).

    Args:
        events:    Iterable of normalized event dicts
                   ({ts,user,item,workspace,operation,kind,cuSeconds,durationMs,throttled}).
        user:      Email string to filter on (case-sensitive — normalize before calling).
        floor_cu:  Absolute CU-seconds floor; an event >= this is always a spike (default 0).
        multiplier: Multiple of the user's p95 an event must exceed to count as a spike
                   (default ``config.activity.baselineSpikeMultiplier``).

    Returns dict:
        {
            user:                 str,
            spikeCount:           int,
            baselineP95CuSeconds: float|None,
            spikeMultiplier:      float,
            spikeThresholdCuSeconds: float|None,
            totalCuSeconds:       float,
            peakCuSeconds:        float,
            spikes:               [{ts, item, operation, kind, cuSeconds}, ...] sorted cu desc,
            topItems:             [{item, cuSeconds}, ...] sorted cu desc,
            byHour:               {hour_int: spike_event_count},
            interactiveVsRefresh: {interactiveCuSeconds, refreshCuSeconds},
        }
    """
    user_events = [e for e in events if e.get("user") == user]
    mult = _SPIKE_MULTIPLIER if multiplier is None else float(multiplier)

    if not user_events:
        return {
            "user": user,
            "spikeCount": 0,
            "baselineP95CuSeconds": None,
            "spikeMultiplier": mult,
            "spikeThresholdCuSeconds": None,
            "totalCuSeconds": 0,
            "peakCuSeconds": 0,
            "spikes": [],
            "topItems": [],
            "byHour": {},
            "interactiveVsRefresh": {"interactiveCuSeconds": 0.0, "refreshCuSeconds": 0.0},
        }

    baseline = compute_baseline(user_events)
    p95 = baseline.get("p95")

    # ANOMALY TEST, not a percentile lookup. The baseline p95 is computed from THE SAME events
    # being tested, so a bare `cu > p95` returns ~5% of the input by construction, forever: 400
    # uniform events reported spikeCount 20 on a user whose behaviour never varied. Requiring a
    # MULTIPLE of p95 makes the answer depend on the shape of the distribution instead of its
    # length — the fix detectors/user_baseline.py already carries, applied here because this
    # module is an MCP tool the agent quotes directly.
    threshold = p95 * mult if p95 is not None else None

    # Treat floor_cu=0 (falsy) as "no absolute floor" — pass None so is_spike only uses the
    # threshold.
    effective_floor = floor_cu if floor_cu else None

    # Identify spike events
    spike_events = [
        e for e in user_events
        if is_spike(e, p95=threshold, floor_cu=effective_floor)
    ]
    # `.get("cuSeconds", 0)` defaults a MISSING key; a key present and explicitly None still yields
    # None, and None breaks `sorted`, `sum` and `max` alike. Tier-1 activity events carry
    # cuSeconds=None on EVERY row -- the codebase documents this in three places -- so this MCP tool,
    # which the agent quotes directly, raised TypeError on the most ordinary event source it has.
    # Aggregates skip the unmeasured rows and the result DISCLOSES how many were skipped, because a
    # total silently computed over a third of the rows is worse than one labelled incomplete.
    def _cu(e):
        v = e.get("cuSeconds")
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    _costed = [e for e in user_events if _cu(e) is not None]
    _uncosted = len(user_events) - len(_costed)

    spike_events_sorted = sorted(spike_events, key=lambda e: _cu(e) if _cu(e) is not None else -1.0,
                                 reverse=True)

    # totalCuSeconds and peakCuSeconds over every MEASURED user event (not just spikes)
    total_cu = sum(_cu(e) for e in _costed)
    peak_cu = max((_cu(e) for e in _costed), default=0)

    # spikes list — only the needed fields, sorted by cuSeconds desc
    spikes = [
        {
            "ts": e.get("ts", ""),
            "item": e.get("item"),
            "operation": e.get("operation", ""),
            "kind": e.get("kind", ""),
            "cuSeconds": _cu(e),          # null, never 0, when the row carries no cost
        }
        for e in spike_events_sorted
    ]

    # topItems — sum cuSeconds per item across ALL user events, sorted desc
    item_totals = {}
    for e in user_events:
        item = e.get("item") or ""
        item_totals[item] = item_totals.get(item, 0.0) + (_cu(e) or 0.0)
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
    interactive_cu = sum(_cu(e) for e in _costed if e.get("kind") == "interactive")
    refresh_cu = sum(_cu(e) for e in _costed if e.get("kind") == "refresh")

    return {
        "user": user,
        "spikeCount": len(spikes),
        # Load-bearing: every CU aggregate below is over the MEASURED rows only. Without this the
        # caller cannot tell "this user cost little" from "we could not price most of their work".
        "eventsWithoutCost": _uncosted,
        "cuAggregatesComplete": _uncosted == 0,
        # The bar each spike had to clear, so the count can be explained rather than trusted.
        "baselineP95CuSeconds": p95,
        "spikeMultiplier": mult,
        "spikeThresholdCuSeconds": threshold,
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
