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
from datetime import datetime, timedelta, timezone

from ..config import DEFAULT_CONFIG
from ..investigation.baseline import compute_baseline, compare_to_baseline
from ..timefmt import parse_iso_utc


def _num(v):
    """Reject bool + non-finite (repo numeric-guard convention); else the numeric value."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _now_utc():
    return datetime.now(timezone.utc)


def _fresh_enough(baseline, now, max_age_days):
    """True when a baseline row is recent enough to compare against.

    The nightly bootstrap fails QUIETLY (it returns ``rowsWritten=0`` rather than raising), and
    ``upsert_many`` never deletes, so a user who stops appearing keeps their old row forever.
    Without an age check a three-week-old p95 keeps getting presented to a human as "their own
    baseline". A row with no parseable ``asOf`` is treated as fresh: ``asOf`` is advisory and an
    unstamped row (e.g. hand-seeded, or written by an older build) should degrade to the previous
    behaviour rather than silently disabling the whole layer.
    """
    if not baseline or max_age_days is None or max_age_days <= 0:
        return True
    stamped = parse_iso_utc(baseline.get("asOf"))
    if stamped is None:
        return True
    return (now - stamped) <= timedelta(days=max_age_days)


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


def detect_user_baseline_deviation_precomputed(facts, config=None, baseline_store=None,
                                                now=None):
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
    if not events:
        return []
    _dflt = DEFAULT_CONFIG["activity"]
    thr = (config.get("activity") or _dflt)

    def _cfg(key):
        v = thr.get(key)
        return _dflt[key] if v is None else v

    min_history = _cfg("baselineMinHistory")
    multiplier = float(_cfg("baselineSpikeMultiplier"))
    estate_multiplier = float(_cfg("baselineSpikeEstateMultiplier"))
    floor_cu = float(_cfg("baselineSpikeFloorCuSeconds"))
    max_age_days = float(_cfg("baselineMaxAgeDays"))

    # Load the WHOLE baseline set once per run. Both lookups used to be per-event; the estate
    # row was already hoisted but get_user was not, which meant one Spark query per event.
    try:
        estate = baseline_store["get_estate"]()
    except Exception as exc:
        print(f"[baseline] estate lookup failed ({type(exc).__name__}: {exc})")
        estate = None
    per_user = {}
    # Track whether the BULK PATH IS IN PLAY, not whether it returned anything. Gating the
    # lookup on `if per_user:` (truthiness) meant an empty-but-successful load — a freshly
    # created table with only an estate row, or every user filtered out by min_history, both
    # very likely in the first days after enabling this — sent EVERY event down the per-user
    # `get_user` path: one spark.sql().collect() per event, i.e. the exact thousands-of-
    # round-trips regression the bulk load exists to remove, on the path where there is nothing
    # to find anyway. Silent, because the fallback swallows exceptions; the only symptom is a
    # 5-minute job taking minutes and overlapping the next run.
    bulk = "get_all_users" in baseline_store
    if bulk:
        try:
            per_user = baseline_store["get_all_users"]() or {}
        except Exception as exc:
            print(f"[baseline] bulk user load failed ({type(exc).__name__}: {exc})")
            per_user = {}

    # ``now`` is injectable so a test can pin it: the staleness check compares against
    # wall-clock, which would make any historical fixture look stale.
    now = now if now is not None else _now_utc()
    if estate is not None and not _fresh_enough(estate, now, max_age_days):
        # Every row shares the nightly job's asOf, so N consecutive nightly failures expire ALL
        # layers at once and the feature goes 100% silent. Say so out loud — otherwise "no
        # alerts" is indistinguishable from "nothing wrong".
        print(f"[baseline] estate baseline is stale (asOf={estate.get('asOf')}, "
              f"maxAgeDays={max_age_days}) — falling through to silence for cold-start users")
        estate = None
    if per_user and all(not _fresh_enough(b, now, max_age_days) for b in per_user.values()):
        print(f"[baseline] ALL {len(per_user)} personalized baselines are stale "
              f"(maxAgeDays={max_age_days}) — the nightly bootstrap has not succeeded recently")

    def _lookup(user):
        """Personalized baseline for ``user``, from the bulk map when available."""
        if bulk:
            return per_user.get(user)
        # Fallback for a store without get_all_users (older adapter / a custom fake).
        try:
            return baseline_store["get_user"](user)
        except Exception:
            return None

    flags = []
    for ev in events:
        user = ev.get("user")
        if not user:
            continue
        cu = _num(ev.get("cuSeconds"))
        if cu is None:
            continue

        # LAYER 1: personalized baseline — enough samples, has a p95, and not stale.
        personalized = _lookup(user)
        baseline = None
        source = None
        if (personalized and personalized.get("count") is not None
                and personalized["count"] >= min_history
                and personalized.get("p95") is not None
                and _fresh_enough(personalized, now, max_age_days)):
            baseline = personalized
            source = "personalized"
        # LAYER 2: estate-wide fallback for cold-start / under-sampled / stale users.
        elif (estate and estate.get("count") is not None
              and estate.get("p95") is not None):
            baseline = estate
            source = "estate"
        # LAYER 3: silent — no usable baseline, no alert. Never invent an anomaly.
        if baseline is None:
            continue

        # ANOMALY TEST (not a percentile lookup). `cu > p95` alone fires on ~5% of all events
        # by construction — see config.baselineSpikeMultiplier. Require BOTH a multiple of the
        # baseline AND an absolute floor, so neither a tiny baseline nor a big-but-normal user
        # generates noise.
        #
        # The estate layer uses a MUCH larger multiple: a correctly-computed estate p95 is small,
        # so the shared floor would otherwise be the only gate and this layer would just
        # re-report routine heavy users (see config.baselineSpikeEstateMultiplier).
        p95 = baseline["p95"]
        mult = multiplier if source == "personalized" else estate_multiplier
        if not (cu > p95 * mult and cu >= floor_cu):
            continue

        comparison = compare_to_baseline(cu, baseline)
        item = ev.get("item") or "unknown item"
        operation = ev.get("operation") or "operation"
        ratio = round(cu / p95, 2) if p95 else None
        p95_out = round(p95, 2)
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
                "baselineSource": source, "baselineAsOf": baseline.get("asOf"),
                "spikeMultiplier": mult, "ratioVsP95": ratio,
                "deltaVsP50Pct": comparison.get("deltaVsP50Pct"),
            },
            "what": (f"{user} ran \"{operation}\" on \"{item}\" costing {cu_out} CPU-s — "
                     f"{ratio}x {source_phrase} of {p95_out} CPU-s "
                     f"(from {baseline.get('count')} historical operations)."),
        })
    return flags
