"""Per-user concentration detector — answers "which users went over N% of CU".

Reads ``facts["users"]`` (the per-user rollup the Log Analytics / Workspace Monitoring collectors
emit) and flags any user whose share of monitored CU is >= ``config.capacity.concentrationPct``.
USER-level complement to ``concentration.py`` (which is ITEM-level): one names the heavy *item*,
this names the heavy *person*.

When no single user crosses the threshold, it still emits one ``capacity.user-ranking`` info flag
naming the top consumers — so "who is heaviest today?" always gets a concrete answer (and it's clear
the concentration is spread across many users on an item, not driven by one account).

``sharePct`` is a CPU-proxy share of monitored CU (not the authoritative capacity CU%) — it ranks
who is heaviest, which is exactly what this detector reports.
"""
import math

from ..config import DEFAULT_CONFIG
from .system_item_kinds import is_system_item_kind


def _fmt(x):
    """70.0 -> '70', 32.4 -> '32.4' (one decimal)."""
    try:
        return str(int(x)) if x == int(x) else str(round(x, 1))
    except (TypeError, ValueError):
        return str(x)


def _share(u):
    s = u.get("sharePct")
    return s if isinstance(s, (int, float)) and not isinstance(s, bool) and math.isfinite(s) else None


def _cap_pct(facts):
    """Capacity peak CU% from the capacity-events collector, if present (else None)."""
    v = (facts.get("capacity") or {}).get("peakCuPct")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def detect_user_concentration(facts, config=None):
    config = config or DEFAULT_CONFIG
    facts = facts or {}
    users = [u for u in (facts.get("users") or []) if u.get("user")]
    if not users:
        return []

    # N6 fix (Task 8.1, 2026-07-29): cross-reference item names from facts["items"] against
    # system_item_kinds to build a set of system-item NAMES.  Users whose topItems are ALL
    # system items are excluded -- their apparent concentration is structural (e.g. a service
    # identity listening on an EventStream 24/7), not a real finding.  When facts["items"] is
    # absent or carries no kind data, the set is empty and nobody is excluded (conservative).
    system_item_names = {
        (it.get("name") or "").lower()
        for it in (facts.get("items") or [])
        if is_system_item_kind(it.get("kind"))
    }
    system_item_names.discard("")  # don't match unnamed items

    def _is_pure_system_user(u):
        """True iff every item in this user's topItems is a known system item (by name)."""
        top = u.get("topItems") or []
        if not top or not system_item_names:
            return False  # no topItems or no system names -> conservative: keep the user
        return all((ti.get("name") or "").lower() in system_item_names for ti in top)

    users = [u for u in users if not _is_pure_system_user(u)]
    if not users:
        return []

    min_share = config["capacity"]["concentrationPct"]
    cap_pct = _cap_pct(facts)
    # When capacity CU% is known (capacity-events wired), estimate each user's share OF CAPACITY:
    #   est % of capacity ≈ (user's share of monitored CU) × (capacity utilization %).
    # An ESTIMATE (window-avg share × peak util; LA covers semantic-model engine load only), not a
    # direct per-user CU measurement. Without capacity CU%, fall back to share of monitored CU.
    label = "capacity CU (est.)" if cap_pct is not None else "monitored CU"

    def metric(u):
        s = _share(u)
        if s is None:
            return None
        return s * cap_pct / 100.0 if cap_pct is not None else s

    ranked = sorted(users, key=lambda u: -(metric(u) or 0))
    flags = []

    over = [u for u in ranked if (metric(u) or 0) >= min_share]
    for u in over:
        val = metric(u)
        top_item = (u.get("topItems") or [{}])[0].get("name") or "unknown item"
        flags.append({
            "type": "capacity.user-concentration",
            "resource": u["user"],
            "when": u.get("observedAt") or "",
            "evidence": {
                "sharePct": int(val) if val == int(val) else round(val, 1),
                "monitoredSharePct": round(_share(u), 1),
                "capacityPeakPct": cap_pct, "estimated": cap_pct is not None,
                "cuSeconds": u.get("cuSeconds"), "topItems": u.get("topItems"),
                "itemCount": u.get("itemCount"),
            },
            "what": f"{u['user']} is driving ~{_fmt(val)}% of {label} — mostly via \"{top_item}\".",
        })

    if not over:
        # No single user over threshold — name the top consumers so the question is still answered.
        top = ranked[:3]
        listed = ", ".join(f"{u['user']} (~{_fmt(metric(u) or 0)}%)" for u in top)
        flags.append({
            "type": "capacity.user-ranking",
            "resource": "top-users",
            "when": "",
            "evidence": {
                "topUsers": [{"user": u["user"], "sharePct": round(metric(u) or 0, 1)} for u in top],
                "userCount": len(users), "capacityPeakPct": cap_pct, "estimated": cap_pct is not None,
            },
            "what": (f"No single user is over {_fmt(min_share)}% of {label} "
                     f"(load is spread across {len(users)} users). Top consumers: {listed}."),
        })

    return flags
