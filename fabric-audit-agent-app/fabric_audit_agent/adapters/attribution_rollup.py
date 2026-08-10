"""Shared per-(workspace, item) + per-user attribution rollup.

Both the Log Analytics and Workspace Monitoring collectors feed their query rows through this so
they emit an identical shape — ``items[]`` (the input for the item ``concentration`` detector) and
``users[]`` (per-user rollup used by attribution / diagnosis, e.g. the hot-user step in
``investigation/diagnose.py``). Keeping one rollup means the two sources can't drift, and it's the
single place that has to be source-tolerant.

Tolerance (why this exists):
  * column spelling differs by source — Log Analytics names the item ``ArtifactName`` and the
    workspace ``PowerBIWorkspaceName``; the Fabric Workspace-Monitoring Eventhouse uses ``ItemName``
    / ``WorkspaceName`` and carries the user as a structured ``Identity`` field (not ``ExecutingUser``).
  * the cost column is ``CpuTimeMs`` when present, else ``DurationMs`` (a wall-clock proxy) — the live
    SemanticModelLogs table seen in the field did not expose ``CpuTimeMs``.
  * a real Kusto/Logs client returns a list of dict rows, but we never want a stray non-dict value
    (or a mis-shaped query result) to crash the whole audit — such rows are skipped.

CPU/duration time is a **proxy** for CU (engine time, AS-only scope): it ranks the driving users
correctly but is not the authoritative capacity CU share. That share comes from Capacity
Metrics / Capacity Events and wins on merge.

B2 blank-user fallback (Task 4.1):
  When ``ExecutingUser``/``Identity`` is blank, two fallback tiers attempt resolution:
    1. **Activity Events cross-reference** — match by item name + timestamp (±60 s) against
       ``activity_events`` (the mapped Activity Events list). Strongest non-direct source.
    2. **Item owner fallback** — use ``configuredBy`` / ``owner`` from ``items_metadata``.
       Weaker: item owner ≠ who ran this specific operation, but better than blank.
  Each user entry carries ``attributionSource``: ``"direct"`` | ``"activity-crossref"`` |
  ``"item-owner"`` | ``"unresolved"``.
"""
from datetime import datetime
from ..timefmt import parse_iso_utc

# Attribution-source strength: lower = stronger (direct beats all).
_ATTRIBUTION_STRENGTH = {"direct": 0, "activity-crossref": 1, "item-owner": 2, "unresolved": 3}


def _stronger_source(a, b):
    """Return the stronger (more specific/trustworthy) attribution source."""
    return a if _ATTRIBUTION_STRENGTH.get(a, 99) <= _ATTRIBUTION_STRENGTH.get(b, 99) else b


def _parse_ts(val):
    """Best-effort ISO-8601 timestamp parse. Returns ``datetime`` or ``None``.

    Delegates to the repo's canonical parser rather than hand-rolling it. The hand-rolled version
    swapped ``Z`` for ``+00:00`` and handed the string straight to ``fromisoformat``, which on
    Python 3.10 -- what the serverless JOB COMPUTE runs, and the sweep job is where Log Analytics
    rows are collected -- accepts only 3 or 6 fractional digits. Real LA ``TimeGenerated`` values
    carry SEVEN (``2026-08-05T13:52:07.3079171Z``), so every parse raised, was caught, returned
    None, and silently skipped the activity cross-reference. It worked locally on 3.12 and in the
    App on 3.11, so nothing surfaced it. ``timefmt.parse_iso_utc`` trims the fraction to
    microseconds first and exists precisely for this.
    """
    return parse_iso_utc(val)


def _ts_delta_seconds(a, b):
    """Absolute difference in seconds between two datetimes, tz-tolerant. None on failure."""
    if a is None or b is None:
        return None
    # Make both offset-aware or both offset-naive so subtraction doesn't raise.
    if a.tzinfo and not b.tzinfo:
        a = a.replace(tzinfo=None)
    elif b.tzinfo and not a.tzinfo:
        b = b.replace(tzinfo=None)
    try:
        return abs((a - b).total_seconds())
    except TypeError:
        return None


def resolve_blank_user(name, ws, ts_str, activity_events, items_metadata,
                       window_seconds=60):
    """Resolve a blank user identity via two fallback tiers.

    Tier 1 — Activity Events cross-reference: match by item name (+ workspace when both are
    known) and timestamp within ``±window_seconds``. Picks the closest match.

    Tier 2 — Item owner: ``configuredBy`` or ``owner`` from ``items_metadata``.

    Returns ``(user_string_or_None, attribution_source)``.
    """
    # Tier 1: cross-reference against Activity Events by item + timestamp
    if activity_events and ts_str:
        row_ts = _parse_ts(ts_str)
        if row_ts is not None:
            best_user, best_delta = None, window_seconds + 1
            for ae in activity_events:
                if ae.get("item") != name:
                    continue
                ae_ws = ae.get("workspace")
                if ae_ws is not None and ws is not None and str(ae_ws).lower() != str(ws).lower():
                    continue
                ae_user = str(ae.get("user") or ae.get("UserId") or "").strip()
                if not ae_user:
                    continue
                ae_ts = _parse_ts(ae.get("time") or ae.get("CreationTime"))
                delta = _ts_delta_seconds(row_ts, ae_ts)
                if delta is not None and delta <= window_seconds and delta < best_delta:
                    best_delta = delta
                    best_user = ae_user
            if best_user:
                return best_user, "activity-crossref"

    # Tier 2: item owner fallback
    if items_metadata:
        for it in items_metadata:
            if it.get("name") != name:
                continue
            it_ws = it.get("workspace")
            if it_ws is not None and ws is not None and str(it_ws).lower() != str(ws).lower():
                continue
            owner = it.get("configuredBy") or it.get("owner")
            if owner:
                return str(owner).strip(), "item-owner"

    return None, "unresolved"


def identity_email(value):
    """Resolve a user string from whatever the source put in the user column.

    Workspace Monitoring's ``Identity`` arrives as a structured object ({"Email": ...} /
    {"email": ...} / {"UserPrincipalName": ...}); Log Analytics' ``ExecutingUser`` is a plain
    string. Return the cleanest user handle we can, else the value unchanged."""
    if isinstance(value, dict):
        return (value.get("Email") or value.get("email")
                or value.get("UserPrincipalName") or value.get("upn") or value.get("User"))
    return value


def _row(r, *names):
    """First key present and non-None — tolerant of LA vs Eventhouse column spellings."""
    for n in names:
        if r.get(n) is not None:
            return r[n]
    return None


def rollup_attribution(rows, top_n=3, ws_label="",
                       activity_events=None, items_metadata=None):
    """rows -> ``{"items": [...], "users": [...]}``. Pure; safe on empty/malformed input.

    N7 fix: ``attributionMode`` now distinguishes ``"cost-cpu"`` (true ``CpuTimeMs``) from
    ``"cost-duration"`` (the weaker ``DurationMs`` wall-clock fallback) rather than collapsing
    both into a single ``"cost"`` label — downstream code (and the agent's own wording) can now
    tell which proxy actually backs a given number instead of treating both as equally grounded.

    A3 fix: each item/user now carries ``truncated`` (bool) — True when more distinct
    users/items existed than ``top_n`` kept, so downstream logic and responses can say
    "showing top N of possibly more" instead of silently implying the list is complete.

    B2 blank-user fallback (Task 4.1): when ``activity_events`` and/or ``items_metadata`` are
    supplied, blank-user rows are resolved via two fallback tiers (Activity Events cross-
    reference, then item owner). Every user entry carries ``attributionSource`` indicating how
    the identity was determined.
    """
    groups = {}
    by_user = {}   # user -> {cpu, items{name: cpu}, hasCpuTime, source} — the per-user rollup
    for r in rows or []:
        if not isinstance(r, dict):
            continue   # defensive: never crash on a stray non-dict row from a real query
        name = _row(r, "Item", "item", "name", "ItemName", "ArtifactName")
        if not name:
            continue
        ws = _row(r, "Workspace", "workspace", "WorkspaceName", "PowerBIWorkspaceName") or ws_label
        user = identity_email(_row(r, "ExecutingUser", "user", "Identity"))

        # B2: determine attribution source and resolve blank users via fallback tiers.
        attr_source = "direct"
        if not user or not str(user).strip():
            ts_str = _row(r, "TimeGenerated", "Timestamp", "ts", "time", "CreationTime")
            user, attr_source = resolve_blank_user(
                name, ws, ts_str, activity_events, items_metadata,
            )

        # Cost column is milliseconds (CpuTimeMs / DurationMs) -> convert to CU-seconds so this
        # matches ``normalize_event``'s scale (this rollup previously emitted MILLISECONDS labelled
        # as cuSeconds, ~1000x off from the event path). ``cuSeconds`` input is already seconds.
        # N7: track whether THIS row's cost came from true CpuTimeMs or the DurationMs fallback,
        # so the group-level mode can reflect what actually backed the numbers.
        # ``cpuMs`` is TRUE CPU TIME, not a duration proxy: the deployed default LA query
        # (collector_log_analytics._build_default_kql) ends with
        # ``| summarize cpuMs=sum(CpuTimeMs) by ...``, i.e. cpuMs IS sum(CpuTimeMs) under an alias.
        # Grouping it with DurationMs in the fallback meant is_true_cpu was False for EVERY row in
        # production, so every item and user shipped attributionMode="cost-duration" while the
        # numbers were genuine CPU time. The mislabel erred safe (it under-claimed), but the
        # proxy/true-CU indicator, the confidence badge and the card copy all read it, so the
        # product understated its own evidence on every sweep.
        cpu_time_ms = _row(r, "CpuTimeMs", "cpuMs")
        raw_ms = cpu_time_ms if cpu_time_ms is not None else _row(r, "DurationMs")
        cs = r.get("cuSeconds")
        cpu = (raw_ms / 1000.0) if raw_ms is not None else (cs if cs is not None else 0)
        is_true_cpu = cpu_time_ms is not None
        g = groups.setdefault((str(ws).lower(), str(name).lower()),
                              {"workspace": ws, "name": name, "users": {}, "cpu": 0,
                               "hasCpuTime": False, "userSources": {}})
        g["cpu"] += cpu
        g["hasCpuTime"] = g["hasCpuTime"] or is_true_cpu
        if user:
            g["users"][user] = g["users"].get(user, 0) + cpu
            # Track the strongest attribution source per user within this group.
            prev = g["userSources"].get(user)
            g["userSources"][user] = _stronger_source(attr_source, prev) if prev else attr_source

            u = by_user.setdefault(user, {"user": user, "cpu": 0, "items": {},
                                          "hasCpuTime": False, "source": attr_source})
            u["cpu"] += cpu
            u["hasCpuTime"] = u["hasCpuTime"] or is_true_cpu
            u["items"][name] = u["items"].get(name, 0) + cpu
            # Update global per-user source if this row's source is stronger.
            u["source"] = _stronger_source(attr_source, u["source"])

    total = sum(g["cpu"] for g in groups.values())

    items = []
    for g in groups.values():
        ranked = sorted(
            ({"user": u, "cuSeconds": round(c, 3),
              "attributionSource": g["userSources"].get(u, "direct")}
             for u, c in g["users"].items()),
            key=lambda x: -x["cuSeconds"])
        user_count = len(ranked)
        items.append({
            "workspace": g["workspace"], "name": g["name"], "cuSeconds": round(g["cpu"], 3),
            "sharePct": (g["cpu"] / total * 100) if total else 0,
            "topUsers": ranked[:top_n], "userCount": user_count,
            "attributionMode": "cost-cpu" if g["hasCpuTime"] else "cost-duration",
            "truncated": user_count > top_n,
        })

    users = []
    for u in by_user.values():
        top_items = sorted(({"name": n, "cuSeconds": round(c, 3)} for n, c in u["items"].items()),
                           key=lambda x: -x["cuSeconds"])
        item_count = len(top_items)
        users.append({
            "user": u["user"], "cuSeconds": round(u["cpu"], 3),
            "sharePct": (u["cpu"] / total * 100) if total else 0,
            "topItems": top_items[:top_n], "itemCount": item_count,
            "attributionMode": "cost-cpu" if u["hasCpuTime"] else "cost-duration",
            "truncated": item_count > top_n,
            "attributionSource": u.get("source", "direct"),
        })
    users.sort(key=lambda x: -x["cuSeconds"])

    return {"items": items, "users": users}
