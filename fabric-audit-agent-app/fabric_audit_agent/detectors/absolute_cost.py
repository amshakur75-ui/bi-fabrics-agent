"""Absolute-cost detector. tightening.md Part 1a (Sub-plan 1 of the alerting redesign,
``docs/superpowers/specs/2026-08-07-alerting-redesign-and-plugin-parity-design.md``).

Flags any SINGLE operation whose absolute cost is high -- duration >= a threshold OR
cuSeconds >= a threshold -- independent of any share-of-capacity. "A single query running
611s or 800s is worth flagging on its own, regardless of what percentage it represents."
Pure Log Analytics fact, zero capacity data: this detector never reads ``facts["capacity"]``
and never computes or mentions a capacity percentage.

Contract: reads ``facts["events"]``, a list of normalize_event-shaped dicts (see
``investigation/events.py``: ``user``, ``item``, ``operation``, ``durationMs``, ``cuSeconds``,
``queryText``, ...). One flag per qualifying operation.

WIRED (TASK 1-WIRE, 2026-08-07): ``job.build_collector_from_env`` now attaches a bounded
(``job._EVENTS_CAP`` = 5000, costliest-first) list of normalized events onto ``facts["events"]``
via ``job._build_events_collector`` (same builder as ``adapters/collector_events_la.py``, which the
MCP query tools' ``spike_events`` / ``raw_events`` already use), and ``adapters/collector_merge.py``
folds it across sources. Gated on the same env as the summarized Log Analytics collector
(``FABRIC_LA_WORKSPACE_ID`` + ``FABRIC_CLIENT_ID``); fails open to ``[]`` on any pull error. See
``tests/test_events_wiring.py``.
"""
import math

from ..config import DEFAULT_CONFIG


def _num(v):
    """Reject bool + non-finite (repo numeric-guard convention); else the numeric value."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def detect_absolute_cost(facts, config=None):
    config = config or DEFAULT_CONFIG
    facts = facts or {}
    events = facts.get("events") or []
    thr = (config.get("activity") or DEFAULT_CONFIG["activity"])
    slow_seconds = thr["slowOperationSeconds"] if thr.get("slowOperationSeconds") is not None else DEFAULT_CONFIG["activity"]["slowOperationSeconds"]
    high_cu = thr["highCuSeconds"] if thr.get("highCuSeconds") is not None else DEFAULT_CONFIG["activity"]["highCuSeconds"]

    flags = []
    for ev in events:
        duration_ms = _num(ev.get("durationMs"))
        cu_seconds = _num(ev.get("cuSeconds"))
        duration_seconds = duration_ms / 1000.0 if duration_ms is not None else None

        slow = duration_seconds is not None and duration_seconds >= slow_seconds
        costly = cu_seconds is not None and cu_seconds >= high_cu
        if not (slow or costly):
            continue

        user = ev.get("user") or "unknown user"
        item = ev.get("item") or "unknown item"
        operation = ev.get("operation") or "operation"
        dur_out = round(duration_seconds, 1) if duration_seconds is not None else None
        cu_out = round(cu_seconds, 1) if cu_seconds is not None else None

        flags.append({
            "type": "activity.slow-operation",
            "resource": user,
            "when": ev.get("ts") or "",
            "evidence": {
                "user": ev.get("user"), "item": ev.get("item"), "operation": ev.get("operation"),
                "durationMs": ev.get("durationMs"), "cuSeconds": ev.get("cuSeconds"),
                "durationSeconds": dur_out,
            },
            "what": (f"{user} ran an operation on \"{item}\" that took "
                     f"{dur_out if dur_out is not None else '?'}s "
                     f"({cu_out if cu_out is not None else '?'} CU-s) — {operation}."),
        })
    return flags
