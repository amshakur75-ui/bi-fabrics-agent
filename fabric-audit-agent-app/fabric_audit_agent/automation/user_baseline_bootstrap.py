"""Per-user + estate-wide CPU-seconds baseline aggregation (Design A' — Phase B, task B1).

Pure aggregation over a 14-day activity-history rowset. Groups by user, computes p50/p95/count
plus estate-wide aggregates, and returns rows ready to upsert into the ``user_baseline`` Delta
table.

Runs OFFLINE (nightly Databricks Job), NOT inline with the 5-min sweep. The sweep-time detector
(``detect_user_baseline_deviation_precomputed`` — added in B2) reads the precomputed baseline
from the Delta table rather than recomputing per tick, so a user's spike is decided against a
14-day p95 without re-scanning the raw history each run.

Rows carry a ``scope`` discriminator so a single table serves both layers of the fallback:

  - ``scope = "user"``: personalized p95 for each user (default cohort).
  - ``scope = "estate"``: single row across all users, used as the layer-2 fallback for
    cold-start users who don't yet have ``min_history`` operations of their own.

An estate row is ALWAYS emitted when the input is non-empty (even if no individual user meets
``min_history``), so cold-start coverage never gaps.
"""
import math


def _percentile(sorted_vals, pct):
    """Linear-interpolated percentile — same helper convention as investigation.baseline._percentile.

    Mirrors that implementation so a baseline computed here matches one computed inline by the
    live detector fallback. Empty input returns None (caller decides how to handle).
    """
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = pct / 100.0 * (len(sorted_vals) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_vals[lo]
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _num(v):
    """Numeric guard: bool/None/non-finite → None. Preserves 0.0 (a real zero cost)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def build_baselines(events, *, min_history=20, as_of=None):
    """Aggregate 14-day raw event rows into per-user + estate baseline rows.

    Args:
        events:       Iterable of raw event dicts. Each row needs ``user`` (str) and
                      ``cuSeconds`` (numeric — CPU-time proxy from Log Analytics, seconds).
                      Rows missing either are skipped silently.
        min_history:  Per-user minimum sample size before that user gets a row. Users with
                      fewer rows fall through to the estate baseline in the detector's
                      3-layer fallback (personalized / estate-wide / silent). Default 20.
        as_of:        Optional ISO-8601 timestamp to stamp on every emitted row. Passed in
                      by the caller (never derived from wall-clock here — this module has to
                      stay deterministic for tests).

    Returns:
        list of baseline row dicts, each:
          {"scope": "user"|"estate", "user": <str|None>, "p50", "p95", "count", "min",
           "max", "asOf": <str|None>}
        The estate row (``scope="estate"``, ``user=None``) is ALWAYS emitted when the input
        has at least one usable numeric ``cuSeconds`` value — even if no individual user
        clears ``min_history`` — so cold-start users always have a fallback baseline to
        compare against.
    """
    events = events or []
    per_user = {}
    all_cus = []
    for e in events:
        user = e.get("user")
        cu = _num(e.get("cuSeconds"))
        if not user or cu is None:
            continue
        per_user.setdefault(user, []).append(cu)
        all_cus.append(cu)

    rows = []
    for user, cus in per_user.items():
        if len(cus) < min_history:
            continue
        cus_sorted = sorted(cus)
        rows.append({
            "scope": "user", "user": user,
            "p50": _percentile(cus_sorted, 50),
            "p95": _percentile(cus_sorted, 95),
            "count": len(cus_sorted),
            "min": cus_sorted[0], "max": cus_sorted[-1],
            "asOf": as_of,
        })
    if all_cus:
        all_sorted = sorted(all_cus)
        rows.append({
            "scope": "estate", "user": None,
            "p50": _percentile(all_sorted, 50),
            "p95": _percentile(all_sorted, 95),
            "count": len(all_sorted),
            "min": all_sorted[0], "max": all_sorted[-1],
            "asOf": as_of,
        })
    return rows


def run_bootstrap_aggregate(collector, *, as_of=None, baseline_store=None):
    """Upsert baseline rows that were ALREADY aggregated by the source.

    Companion to ``run_bootstrap`` for ``adapters.collector_baseline_la``, which computes the
    percentiles server-side in KQL and returns finished rows. There is no Python reduction and no
    row cap, which is the point: the raw-row path had to be capped, and a cost-ordered cap turned
    p95 into ~p99.9 (see that module's docstring).

    ``as_of`` is re-stamped onto every row here so the timestamp is authoritative even if the
    collector was built with a different one. Returns the same summary shape as ``run_bootstrap``.
    """
    rows = list(collector["collect"]() or [])
    if as_of is not None:
        for r in rows:
            r["asOf"] = as_of
    if baseline_store is not None and rows:
        baseline_store["upsert_many"](rows)
    users = sum(1 for r in rows if r.get("scope") == "user")
    return {"rowsWritten": len(rows), "users": users,
            "hasEstate": any(r.get("scope") == "estate" for r in rows), "asOf": as_of}


def run_bootstrap(collector, *, min_history=20, as_of=None, baseline_store=None):
    """Wire-together: pull 14 days of events via ``collector["collect"]()``, aggregate, and
    upsert the resulting rows through the injected store. Returns a summary dict for logging.

    ``collector`` must return a flat list of event dicts (the shape ``build_baselines``
    consumes). In production this is the Log Analytics collector configured with a 14-day
    lookback; in tests you inject a fake that returns a fixture. ``baseline_store`` must
    expose ``upsert_many(rows)``.

    All I/O is DI'd — this function is pure orchestration and never touches HTTP or Spark
    directly.
    """
    events = collector["collect"]()
    rows = build_baselines(events, min_history=min_history, as_of=as_of)
    if baseline_store is not None:
        baseline_store["upsert_many"](rows)
    users = sum(1 for r in rows if r["scope"] == "user")
    return {"rowsWritten": len(rows), "users": users, "hasEstate": any(
        r["scope"] == "estate" for r in rows), "asOf": as_of}
