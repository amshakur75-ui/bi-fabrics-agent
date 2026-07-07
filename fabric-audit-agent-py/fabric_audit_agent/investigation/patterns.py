"""Temporal pattern engine: correlates activity surges with CU% spikes and their driver.

Pure / stdlib — inputs are already-normalized event dicts ({ts,user,item,kind,cuSeconds,...})
and a capacity time-series ({ts, cuPct}).  Deterministic; no ML, no randomness, no datetime.now().

Emits a pattern object ONLY when a meaningful coupling is detected:
  - An activity surge (distinct active users >= SURGE_USER_THRESHOLD) in a time bucket
  - coinciding with or shortly preceding a CU% spike (cuPct >= CU_SPIKE_THRESHOLD)
    within LAG_BUCKETS additional buckets after the surge bucket.

Tunable module constants (all integer/float, no randomness):
  SURGE_USER_THRESHOLD  : minimum distinct users in a bucket to qualify as a surge.
  CU_SPIKE_THRESHOLD    : minimum cuPct in/near the bucket to qualify as a CU spike.
  LAG_BUCKETS           : how many additional buckets after the surge to look for a CU spike.
"""

# ---------------------------------------------------------------------------
# Tunables — keep at module level so callers can monkeypatch in tests
# ---------------------------------------------------------------------------
SURGE_USER_THRESHOLD = 4   # ≥ N distinct users in a bucket = "activity surge"
CU_SPIKE_THRESHOLD   = 70.0  # cuPct ≥ this = "CU spike"
LAG_BUCKETS          = 1   # also look in the next LAG_BUCKETS buckets after the surge


# ---------------------------------------------------------------------------
# Internal timestamp helpers (ISO 8601, stdlib only)
# ---------------------------------------------------------------------------

def _parse_minutes(ts: str) -> int | None:
    """Parse an ISO-8601 timestamp to total minutes since midnight of the date portion.

    Handles 'YYYY-MM-DDTHH:MMZ', 'YYYY-MM-DDTHH:MM:SSZ', 'YYYY-MM-DDTHH:MM:SS.fffZ'.
    Returns None if the string is empty or unparseable.
    Note: we use days-since-epoch * 1440 + hour*60 + minute for correct cross-day ordering.
    """
    if not ts:
        return None
    try:
        # Strip trailing Z/+00:00 for uniformity
        clean = ts.rstrip("Z").split("+")[0]
        date_part, time_part = clean.split("T", 1)
        year, month, day = (int(x) for x in date_part.split("-"))
        time_fields = time_part.split(":")
        hour = int(time_fields[0])
        minute = int(time_fields[1]) if len(time_fields) > 1 else 0
        # Days since a fixed epoch — we use the date numerically for ordering only
        # (absolute value doesn't matter; only relative differences matter)
        day_ordinal = (year * 366 + month * 31 + day)  # monotone proxy, not calendar-exact
        return day_ordinal * 1440 + hour * 60 + minute
    except (ValueError, IndexError, AttributeError):
        return None


def _bucket_key(ts: str, bucket_minutes: int) -> int | None:
    """Return the bucket-start as total-minutes (floored to bucket_minutes)."""
    mins = _parse_minutes(ts)
    if mins is None:
        return None
    return (mins // bucket_minutes) * bucket_minutes


def _minutes_to_iso(total_minutes: int, ref_ts: str) -> str:
    """Convert a bucket key (total minutes) back to a human-readable ISO string.

    Uses the date portion of a reference timestamp that lives inside the bucket.
    Falls back to a compact "HH:MM (bucket)" label if parsing fails.
    """
    try:
        clean = ref_ts.rstrip("Z").split("+")[0]
        date_str = clean.split("T")[0]
        hours = (total_minutes % 1440) // 60
        minutes = total_minutes % 60
        return f"{date_str}T{hours:02d}:{minutes:02d}:00Z"
    except (ValueError, IndexError, AttributeError):
        hours = (total_minutes % 1440) // 60
        minutes = total_minutes % 60
        return f"T{hours:02d}:{minutes:02d}:00Z"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def capacity_patterns(
    events: list[dict],
    capacity_series: list[dict],
    *,
    bucket_minutes: int = 15,
    surge_users: int = SURGE_USER_THRESHOLD,
    cu_spike_pct: float = CU_SPIKE_THRESHOLD,
    lag_buckets: int = LAG_BUCKETS,
    return_diagnostics: bool = False,
):
    """Correlate event-level activity surges with capacity CU% spikes.

    Args:
        events:          Normalized event dicts ({ts,user,item,kind,cuSeconds,...}).
        capacity_series: Capacity CU% time-series ({ts, cuPct}).
        bucket_minutes:  Time-bucket width in minutes (default 15).
        surge_users:     Minimum distinct users in a bucket to qualify as a surge
                          (default ``SURGE_USER_THRESHOLD``).
        cu_spike_pct:    Minimum cuPct in/near the bucket to qualify as a CU spike
                          (default ``CU_SPIKE_THRESHOLD``).
        lag_buckets:     How many additional buckets after the surge to look for a CU spike
                          (default ``LAG_BUCKETS``).
        return_diagnostics: When True, return ``(patterns, diagnostics)`` instead of just
                          ``patterns`` -- see below. Default False preserves the original
                          plain-list return exactly, so existing callers are unaffected.

    Returns:
        When ``return_diagnostics`` is False (default): a list of pattern dicts, one per
        coupled (surge, spike) pair:
        {windowStart, activeUsers, cuPeakPct, drivingItem, drivingUser, kind, narrative}.
        Only buckets with a surge AND a nearby CU spike are emitted.
        Sorted by windowStart ascending for deterministic ordering.

        When ``return_diagnostics`` is True: a tuple ``(patterns, diagnostics)`` where
        ``diagnostics = {"bucketsScanned": int, "maxActiveUsers": int, "maxCuPeakPct": float,
        "thresholds": {"surgeUsers", "cuSpikePct", "bucketMinutes", "lagBuckets"}}`` --
        computed from the SAME bucketing pass as pattern detection (no second re-bucket), so
        an empty ``patterns`` result is always explainable (e.g. "maxActiveUsers 2 vs
        threshold 4") rather than silent.
    """
    if not events or not capacity_series:
        empty_patterns: list[dict] = []
        if return_diagnostics:
            diagnostics = {
                "bucketsScanned": 0,
                "maxActiveUsers": 0,
                "maxCuPeakPct": 0.0,
                "thresholds": {
                    "surgeUsers": surge_users,
                    "cuSpikePct": cu_spike_pct,
                    "bucketMinutes": bucket_minutes,
                    "lagBuckets": lag_buckets,
                },
            }
            return empty_patterns, diagnostics
        return empty_patterns

    # ------------------------------------------------------------------
    # Step 1: Bucket events → per-bucket aggregates
    # ------------------------------------------------------------------
    # Structure: bucket_key -> {users: set, item_cu: {item: float}, user_cu: {user: float},
    #                            kind_counts: {kind: int}, ref_ts: str}
    buckets: dict[int, dict] = {}

    for ev in events:
        bk = _bucket_key(ev.get("ts") if ev.get("ts") is not None else "", bucket_minutes)
        if bk is None:
            continue
        if bk not in buckets:
            buckets[bk] = {
                "users": set(),
                "item_cu": {},
                "user_cu": {},
                "kind_counts": {},
                "ref_ts": ev.get("ts") if ev.get("ts") is not None else "",
            }
        b = buckets[bk]
        user = ev.get("user") if ev.get("user") is not None else ""
        item = ev.get("item") if ev.get("item") is not None else ""
        kind = ev.get("kind") if ev.get("kind") is not None else "interactive"
        cu = ev.get("cuSeconds") if ev.get("cuSeconds") is not None else 0.0

        if user:
            b["users"].add(user)
        b["item_cu"][item] = b["item_cu"].get(item, 0.0) + cu
        b["user_cu"][user] = b["user_cu"].get(user, 0.0) + cu
        b["kind_counts"][kind] = b["kind_counts"].get(kind, 0) + 1

    # ------------------------------------------------------------------
    # Step 2: Bucket capacity_series → per-bucket max cuPct
    # ------------------------------------------------------------------
    # cap_buckets: bucket_key -> float (max cuPct in that bucket)
    cap_buckets: dict[int, float] = {}

    for point in capacity_series:
        bk = _bucket_key(point.get("ts") if point.get("ts") is not None else "", bucket_minutes)
        if bk is None:
            continue
        cu_pct = point.get("cuPct") if point.get("cuPct") is not None else 0.0
        current = cap_buckets.get(bk)
        if current is None or cu_pct > current:
            cap_buckets[bk] = cu_pct

    # ------------------------------------------------------------------
    # Step 3: Detect coupled patterns
    # ------------------------------------------------------------------
    # For each bucket with a surge, look for a CU spike within lag_buckets
    # additional windows (same bucket or next N buckets). Also accumulate diagnostics
    # (bucketsScanned/maxActiveUsers/maxCuPeakPct) from this SAME pass -- no second
    # re-bucket needed even when the caller doesn't want diagnostics back.
    patterns: list[dict] = []
    max_active_users = 0
    max_cu_peak_pct = 0.0
    buckets_scanned = len(buckets)

    for bk in sorted(buckets.keys()):   # sorted → deterministic
        b = buckets[bk]
        active_users = len(b["users"])
        if active_users > max_active_users:
            max_active_users = active_users

        # Check this bucket and the next lag_buckets buckets for a CU spike (used both for
        # pattern detection below AND the maxCuPeakPct diagnostic, so every bucket's nearby
        # peak is tracked regardless of whether it ends up qualifying as a surge).
        cu_peak_pct = 0.0
        for lag in range(lag_buckets + 1):
            candidate_bk = bk + lag * bucket_minutes
            cap_val = cap_buckets.get(candidate_bk)
            if cap_val is not None and cap_val > cu_peak_pct:
                cu_peak_pct = cap_val
        if cu_peak_pct > max_cu_peak_pct:
            max_cu_peak_pct = cu_peak_pct

        if active_users < surge_users:
            continue  # not a surge

        if cu_peak_pct < cu_spike_pct:
            continue  # no CU spike nearby

        # ------ Driving item: highest cumulative cuSeconds in this bucket ------
        # Stable tiebreak: alphabetical item name (deterministic)
        driving_item = max(
            b["item_cu"],
            key=lambda k: (b["item_cu"][k], k),
            default=None,
        )

        # ------ Driving user: highest cumulative cuSeconds in this bucket ------
        # Stable tiebreak: alphabetical user name (deterministic)
        driving_user = max(
            b["user_cu"],
            key=lambda k: (b["user_cu"][k], k),
            default=None,
        )

        # ------ kind: majority kind (interactive / refresh / mixed) ------
        kind_counts = b["kind_counts"]
        interactive_n = kind_counts.get("interactive", 0)
        refresh_n = kind_counts.get("refresh", 0)
        if interactive_n > refresh_n:
            kind = "interactive"
        elif refresh_n > interactive_n:
            kind = "refresh"
        else:
            kind = "mixed"

        # ------ windowStart: ISO string for the bucket start ------
        window_start = _minutes_to_iso(bk, b["ref_ts"])

        # ------ Narrative ------
        item_label = driving_item if driving_item is not None else "unknown"
        user_label = driving_user if driving_user is not None else "unknown"
        cu_label = f"{cu_peak_pct:.1f}" if cu_peak_pct != int(cu_peak_pct) else f"{int(cu_peak_pct)}"
        narrative = (
            f"~{active_users} users active at {window_start} "
            f"→ {cu_label}% CU spike driven by {item_label} (top user: {user_label})"
        )

        patterns.append({
            "windowStart": window_start,
            "activeUsers": active_users,
            "cuPeakPct": cu_peak_pct,
            "drivingItem": driving_item,
            "drivingUser": driving_user,
            "kind": kind,
            "narrative": narrative,
        })

    # Already sorted by windowStart (ascending) because we iterated sorted(buckets.keys())
    if return_diagnostics:
        diagnostics = {
            "bucketsScanned": buckets_scanned,
            "maxActiveUsers": max_active_users,
            "maxCuPeakPct": max_cu_peak_pct,
            "thresholds": {
                "surgeUsers": surge_users,
                "cuSpikePct": cu_spike_pct,
                "bucketMinutes": bucket_minutes,
                "lagBuckets": lag_buckets,
            },
        }
        return patterns, diagnostics
    return patterns
