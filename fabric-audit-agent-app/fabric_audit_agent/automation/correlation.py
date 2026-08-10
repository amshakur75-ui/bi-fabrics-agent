"""B3 — Design A' correlation booster: user-baseline spikes overlapping with active capacity
incidents.

A per-user baseline-deviation flag firing at the SAME TIME as a capacity incident is stronger
signal than either alone — the user's runaway query is likely the driver of the capacity event,
which is exactly the "who caused this" answer the human wants on the alert card. The correlator
pairs the two and attaches the correlated spikes onto the capacity trigger so the composite
capacity-incident card lists them as facts (not a separate additional alert — same card, one
more section).

Pure — no I/O. Given a list of user-spike flags (from
``detect_user_baseline_deviation_precomputed`` this run) and a list of capacity-family triggers,
returns triggers annotated with ``correlatedUserSpikes: [{...}, ...]`` for any user spike whose
timestamp falls within ``window_min`` minutes of the capacity event.
"""
from datetime import datetime, timedelta, timezone

_CAPACITY_FAMILY = ("throttle", "pressure", "overage", "extreme_peak", "throttle_imminent",
                    "capacity_incident")


def _parse_ts(s):
    """Parse an ISO-8601 timestamp; return a tz-aware datetime or None. Accepts trailing "Z"."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _capacity_anchor(trigger, run_at):
    """Return the datetime a capacity trigger should be considered "happening at" for
    correlation. Prefer the actual peak time when the trigger carries one (peakAt); otherwise
    fall back to the run time (the sweep is 5 min, so run_at is a fine approximation)."""
    return _parse_ts(trigger.get("peakAt")) or _parse_ts(run_at)


def _spike_time(flag):
    return _parse_ts(flag.get("when"))


def correlate_user_spikes_with_capacity(user_spikes, capacity_triggers, *, window_min=5,
                                        run_at=None):
    """Annotate each capacity-family trigger with the user spikes that overlap it.

    Args:
        user_spikes:        Iterable of user-baseline-deviation flag dicts (as produced by
                            ``detect_user_baseline_deviation_precomputed``). Each carries
                            ``when`` (ISO-8601), ``resource`` (user), and an ``evidence``
                            block with baselineP95 / cuSeconds / baselineSource.
        capacity_triggers:  Iterable of tier2 triggers. Only capacity-family triggers
                            (throttle / pressure / overage / extreme_peak / throttle_imminent
                            / capacity_incident) receive annotations; others pass through
                            unchanged.
        window_min:         Correlation half-window in minutes (default 5). A user spike at
                            ts is correlated to a capacity event at anchor when
                            |ts - anchor| <= window_min minutes.
        run_at:             Optional fallback ISO-8601 sweep run time — used as the
                            correlation anchor for a capacity trigger that carries no peakAt.

    Returns:
        A new list of triggers. Capacity triggers gain a ``correlatedUserSpikes`` list of
        compact dicts:
          {"user", "when", "cuSeconds", "baselineP95", "ratio", "baselineSource",
           "item", "operation"}
        sorted by ``cuSeconds`` descending so the worst offender is first. ``ratio`` is
        ``cuSeconds / baselineP95`` when both are numeric, rounded to 2 dp; None otherwise.
        Non-capacity triggers are returned as-is (same object identity, unchanged).
    """
    user_spikes = list(user_spikes or [])
    triggers = list(capacity_triggers or [])
    if not user_spikes or not triggers:
        return triggers

    window = timedelta(minutes=max(0.0, float(window_min)))

    # Precompute (ts, spike_summary) pairs once — the same user spike list is compared to
    # every capacity trigger, so parsing per pair would be wasteful.
    parsed = []
    for f in user_spikes:
        ts = _spike_time(f)
        if ts is None:
            continue
        ev = f.get("evidence") or {}
        cu = ev.get("cuSeconds")
        p95 = ev.get("baselineP95")
        try:
            ratio = round(float(cu) / float(p95), 2) if (cu is not None and p95 not in (None, 0)) \
                else None
        except (TypeError, ValueError, ZeroDivisionError):
            ratio = None
        parsed.append((ts, {
            "user": f.get("resource") or ev.get("user"),
            "when": f.get("when"),
            "cuSeconds": cu, "baselineP95": p95, "ratio": ratio,
            "baselineSource": ev.get("baselineSource"),
            "item": ev.get("item"), "operation": ev.get("operation"),
        }))

    out = []
    for t in triggers:
        if t.get("check") not in _CAPACITY_FAMILY:
            out.append(t)
            continue
        anchor = _capacity_anchor(t, run_at)
        if anchor is None:
            out.append(t)
            continue
        matches = [summary for ts, summary in parsed if abs(ts - anchor) <= window]
        if not matches:
            out.append(t)
            continue
        # Sort worst-offender-first by cuSeconds (nulls last) so the composite card leads
        # with the query most likely responsible for the capacity event.
        matches.sort(key=lambda m: (m.get("cuSeconds") is None,
                                    -(m.get("cuSeconds") or 0)))
        t2 = dict(t)
        t2["correlatedUserSpikes"] = matches
        out.append(t2)
    return out
