"""Per-user baseline-deviation HELPER. tightening.md Part 12 Category 4 (Sub-plan 2 of the
alerting redesign, ``docs/superpowers/specs/2026-08-07-alerting-redesign-and-plugin-parity-design.md``).

"A user's single operation duration significantly exceeding THEIR OWN historical baseline -- a
real per-user anomaly signal, computed from their own history, never from a capacity-blended
estimate."

NOT WIRED into ``detect_all`` / ``detectors/__init__.py``. Investigated first (TASK 2d): a
standing detector needs per-user HISTORY -- a series of that user's own past operations to
compute a baseline from (``investigation/baseline.py: compute_baseline``). ``facts`` as built by
``pipeline.run_audit`` (``collector["collect"]()``) and threaded through ``detect_all(facts,
config)`` carries only the CURRENT window's ``facts["events"]`` -- there is no per-user
historical series anywhere in ``facts``, and no history store is passed into ``detect_all``
(compare with ``pipeline.py``'s ``store`` argument, which carries CAPACITY run-history, not
per-user event history). Building this as a standing detector today would mean it can never
fire in production -- a dead detector. Same honest pattern as the refresh silent-success skip
elsewhere in this codebase: expose the pure logic, document why it isn't wired, leave it ready
for when a per-user history store is threaded into ``facts`` (or into ``detect_all`` directly).

This module exposes ``detect_user_baseline_deviation(events, user_history, config=None)`` --
a pure function that takes the history explicitly, so it is fully unit-testable today and
trivially wireable later: a future caller (detector or otherwise) would just need to supply
``user_history`` from a real store, e.g. ``detect_user_baseline_deviation(facts["events"],
history_store.get_all_users())``.
"""
import math

from ..config import DEFAULT_CONFIG
from ..investigation.baseline import compute_baseline, compare_to_baseline


def _num(v):
    """Reject bool + non-finite (repo numeric-guard convention); else the numeric value."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def detect_user_baseline_deviation(events, user_history, config=None):
    """Flag events whose CU-seconds significantly exceeds the SAME user's own baseline p95.

    Args:
        events:        Iterable of normalized event dicts (the current window) --
                        same shape as ``facts["events"]`` elsewhere in ``detectors/``.
        user_history:  Mapping of ``{user: [past_event_row, ...]}`` -- each past row is a dict
                        with at least ``cuSeconds`` (``investigation/baseline.compute_baseline``'s
                        input shape). This is the piece that does not exist in ``facts`` today.
        config:        Optional config dict; reads ``config["activity"]["baselineMinHistory"]``
                        (default: ``DEFAULT_CONFIG``'s value) -- a user needs at least this many
                        historical rows before a baseline is trusted enough to flag against.

    Returns a list of ``activity.user-baseline-deviation``-shaped flag dicts, one per qualifying
    event. Pure Log Analytics + per-user-history fact, zero capacity data: never reads a capacity
    dict and never computes or mentions a capacity percentage.
    """
    config = config or DEFAULT_CONFIG
    events = events or []
    user_history = user_history or {}
    thr = (config.get("activity") or DEFAULT_CONFIG["activity"])
    min_history = thr["baselineMinHistory"] if thr.get("baselineMinHistory") is not None else DEFAULT_CONFIG["activity"]["baselineMinHistory"]

    flags = []
    for ev in events:
        user = ev.get("user")
        if not user:
            continue
        history_rows = user_history.get(user)
        if not history_rows or len(history_rows) < min_history:
            continue

        cu = _num(ev.get("cuSeconds"))
        if cu is None:
            continue

        baseline = compute_baseline(history_rows)
        if not baseline.get("count") or baseline.get("p95") is None:
            continue

        comparison = compare_to_baseline(cu, baseline)
        if not comparison.get("shifted"):
            continue

        item = ev.get("item") or "unknown item"
        operation = ev.get("operation") or "operation"
        p95 = round(baseline["p95"], 2)
        cu_out = round(cu, 2)

        flags.append({
            "type": "activity.user-baseline-deviation",
            "resource": user,
            "when": ev.get("ts") or "",
            "evidence": {
                "user": user, "item": ev.get("item"), "operation": ev.get("operation"),
                "cuSeconds": ev.get("cuSeconds"), "baselineP50": baseline.get("p50"),
                "baselineP95": baseline.get("p95"), "baselineCount": baseline.get("count"),
                "deltaVsP50Pct": comparison.get("deltaVsP50Pct"),
            },
            "what": (f"{user} ran \"{operation}\" on \"{item}\" costing {cu_out} CPU-s — "
                     f"above their own baseline p95 of {p95} CPU-s "
                     f"(from {baseline.get('count')} historical operations)."),
        })
    return flags
