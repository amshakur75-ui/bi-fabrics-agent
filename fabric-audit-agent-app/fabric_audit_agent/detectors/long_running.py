"""Long-running-query CLUSTER detector. tightening.md Part 12 Category 4 (Sub-plan 2 of the
alerting redesign, ``docs/superpowers/specs/2026-08-07-alerting-redesign-and-plugin-parity-design.md``).

"A cluster of multiple long-running queries (e.g. >5 min) against the same item within a short
window — points at the item's design, not any one user." Distinct from
``detectors/absolute_cost.py``'s ``activity.slow-operation`` (which flags a single qualifying
operation on its own): this detector only fires when the SAME item accumulates enough
independently-long operations to be a cluster, regardless of who ran them. Pure Log Analytics
fact, zero capacity data: this detector never reads ``facts["capacity"]`` and never computes or
mentions a capacity percentage.

Contract: reads ``facts["events"]``, a list of normalize_event-shaped dicts (see
``investigation/events.py``: ``user``, ``item``, ``operation``, ``durationMs``, ``cuSeconds``,
``queryText``, ...) -- the same source ``detectors/absolute_cost.py`` and
``detectors/query_shape.py`` read, for consistency.
"""
import math

from ..config import DEFAULT_CONFIG


def _num(v):
    """Reject bool + non-finite (repo numeric-guard convention); else the numeric value."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def detect_long_running_cluster(facts, config=None):
    config = config or DEFAULT_CONFIG
    facts = facts or {}
    events = facts.get("events") or []
    thr = (config.get("activity") or DEFAULT_CONFIG["activity"])
    long_seconds = thr["longRunningSeconds"] if thr.get("longRunningSeconds") is not None else DEFAULT_CONFIG["activity"]["longRunningSeconds"]
    cluster_min = thr["longRunningClusterMin"] if thr.get("longRunningClusterMin") is not None else DEFAULT_CONFIG["activity"]["longRunningClusterMin"]

    groups = {}   # item -> list[event]
    for ev in events:
        duration_ms = _num(ev.get("durationMs"))
        duration_seconds = duration_ms / 1000.0 if duration_ms is not None else None
        if duration_seconds is None or duration_seconds < long_seconds:
            continue
        item = ev.get("item") or "unknown item"
        groups.setdefault(item, []).append(ev)

    flags = []
    for item, group_events in groups.items():
        count = len(group_events)
        if count < cluster_min:
            continue

        users = sorted({ev.get("user") for ev in group_events if ev.get("user")})
        max_duration_ms = max(_num(ev.get("durationMs")) or 0 for ev in group_events)
        sample = group_events[0]

        flags.append({
            "type": "activity.long-running-cluster",
            "resource": item,
            "when": sample.get("ts") or "",
            "evidence": {
                "item": item,
                "count": count,
                "maxDurationMs": max_duration_ms,
                "users": users[:5],
                "sampleOperation": sample.get("operation"),
            },
            "what": (f"\"{item}\" had {count} long-running operations (>= {long_seconds}s each, "
                     f"longest {round(max_duration_ms / 1000.0, 1)}s) — likely an item design "
                     f"issue, not any one user."),
        })
    return flags
