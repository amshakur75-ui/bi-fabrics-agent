"""Per-user baseline-deviation detector — Design A' Phase B (tasks B1 + B2).

"A user's single operation duration significantly exceeding THEIR OWN historical baseline —
a real per-user anomaly signal, computed from their own history, never from a capacity-blended
estimate."

TWO detectors live here:

  1. ``detect_user_baseline_deviation(events, user_history, config)`` — the raw-history
     variant. Takes an explicit ``{user: [past_rows, ...]}`` map and computes the baseline
     inline on each call. Fully unit-testable, used by tests + any caller that already has
     per-user history in memory. NOT WIRED into ``detect_all`` (would be a dead detector —
     ``facts["events"]`` from the collector only carries the CURRENT window; there is no
     per-user history in there).

  2. ``detect_user_baseline_deviation_precomputed(facts, config, baseline_store)`` — the
     production variant (B2). Reads a precomputed baseline (p50/p95/count) from the
     ``user_baseline`` Delta table (populated nightly by ``automation.user_baseline_bootstrap``,
     see B1) rather than recomputing 14 days of history every 5-min sweep. Applies the
     Design A' 3-LAYER FALLBACK:

       Layer 1 — personalized: ``baseline_store["get_user"](user)`` returned a real baseline
                 with count >= min_history → compare against that user's own p95.
       Layer 2 — estate-wide:  personalized missed (cold-start user or count too low) →
                 ``baseline_store["get_estate"]()`` returns the estate-wide baseline → compare
                 against that as a coarse fallback.
       Layer 3 — silent:       both layers absent → NO alert (never fabricate an anomaly
                 signal before we have data — silence is honest, false alerts are not).

     Wired into ``detect_all`` via the optional ``baseline_store`` kwarg (see
     ``detectors/__init__.py``). When ``baseline_store=None`` the detector is skipped
     entirely — safe default before the bootstrap job has populated the table.
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


def detect_user_baseline_deviation_precomputed(facts, config=None, baseline_store=None):
    """B2 (Design A' Phase B) — production baseline detector.

    Reads precomputed baselines from a ``user_baseline`` store (see B1's
    ``context_user_baseline``) rather than computing on the fly. Applies the 3-layer
    fallback (personalized / estate-wide / silent) per event so a user with no
    history yet still gets some coverage, and no one gets a fabricated alert.

    Args:
        facts:            Standard collector-produced facts dict. Reads ``facts["events"]``
                          (list of normalized event rows, same shape the collector
                          produces).
        config:           Optional config dict; reads
                          ``config["activity"]["baselineMinHistory"]`` (default 20 —
                          personalized baselines with fewer samples fall through to the
                          estate layer).
        baseline_store:   The ``{"get_user", "get_estate"}`` store produced by B1's
                          ``context_user_baseline``. When ``None`` the detector is a
                          no-op — the safe default when the nightly bootstrap job has
                          not populated the table yet.

    Returns a list of ``activity.user-baseline-deviation`` flag dicts, each carrying:
      - ``evidence.baselineSource``: "personalized" | "estate" — tells the reader which
                                     layer fired, so the Teams narrative can say "above
                                     this user's own p95" vs "above the estate-wide p95".
    """
    if baseline_store is None:
        return []
    config = config or DEFAULT_CONFIG
    events = (facts or {}).get("events") or []
    thr = (config.get("activity") or DEFAULT_CONFIG["activity"])
    min_history = (thr["baselineMinHistory"] if thr.get("baselineMinHistory") is not None
                   else DEFAULT_CONFIG["activity"]["baselineMinHistory"])

    # Fetch the estate baseline once — it's used across all layer-2 lookups this run and
    # is a single row, so caching avoids N Delta queries when many events fall through.
    try:
        estate = baseline_store["get_estate"]()
    except Exception:
        estate = None

    flags = []
    for ev in events:
        user = ev.get("user")
        if not user:
            continue
        cu = _num(ev.get("cuSeconds"))
        if cu is None:
            continue

        # LAYER 1: personalized baseline.
        try:
            personalized = baseline_store["get_user"](user)
        except Exception:
            personalized = None
        baseline = None
        source = None
        if (personalized and personalized.get("count") is not None
                and personalized["count"] >= min_history
                and personalized.get("p95") is not None):
            baseline = personalized
            source = "personalized"
        # LAYER 2: estate-wide fallback for cold-start users.
        elif (estate and estate.get("count") is not None
              and estate.get("p95") is not None):
            baseline = estate
            source = "estate"
        # LAYER 3: silent — no baseline available yet, no alert.
        if baseline is None:
            continue

        comparison = compare_to_baseline(cu, baseline)
        if not comparison.get("shifted"):
            continue

        item = ev.get("item") or "unknown item"
        operation = ev.get("operation") or "operation"
        p95 = round(baseline["p95"], 2)
        cu_out = round(cu, 2)
        source_phrase = ("their own baseline p95" if source == "personalized"
                         else "the estate-wide p95 (their personalized baseline isn't ready yet)")

        flags.append({
            "type": "activity.user-baseline-deviation",
            "resource": user,
            "when": ev.get("ts") or "",
            "evidence": {
                "user": user, "item": ev.get("item"), "operation": ev.get("operation"),
                "cuSeconds": ev.get("cuSeconds"),
                "baselineP50": baseline.get("p50"), "baselineP95": baseline.get("p95"),
                "baselineCount": baseline.get("count"),
                "baselineSource": source,
                "deltaVsP50Pct": comparison.get("deltaVsP50Pct"),
            },
            "what": (f"{user} ran \"{operation}\" on \"{item}\" costing {cu_out} CPU-s — "
                     f"above {source_phrase} of {p95} CPU-s "
                     f"(from {baseline.get('count')} historical operations)."),
        })
    return flags
