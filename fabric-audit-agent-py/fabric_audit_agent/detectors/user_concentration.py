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


def _fmt(x):
    """70.0 -> '70', 32.4 -> '32.4' (one decimal)."""
    try:
        return str(int(x)) if x == int(x) else str(round(x, 1))
    except (TypeError, ValueError):
        return str(x)


def _share(u):
    s = u.get("sharePct")
    return s if isinstance(s, (int, float)) and not isinstance(s, bool) and math.isfinite(s) else None


def detect_user_concentration(facts, config=None):
    config = config or DEFAULT_CONFIG
    users = [u for u in ((facts or {}).get("users") or []) if u.get("user")]
    if not users:
        return []

    min_share = config["capacity"]["concentrationPct"]
    ranked = sorted(users, key=lambda u: -(_share(u) or 0))
    flags = []

    over = [u for u in ranked if (_share(u) or 0) >= min_share]
    for u in over:
        top_item = (u.get("topItems") or [{}])[0].get("name") or "unknown item"
        share = _share(u)
        flags.append({
            "type": "capacity.user-concentration",
            "resource": u["user"],
            "when": u.get("observedAt") or "",
            "evidence": {
                "sharePct": int(share) if share == int(share) else round(share, 1),
                "cuSeconds": u.get("cuSeconds"), "topItems": u.get("topItems"),
                "itemCount": u.get("itemCount"),
            },
            "what": f"{u['user']} is driving {_fmt(share)}% of monitored CU — mostly via \"{top_item}\".",
        })

    if not over:
        # No single user over threshold — name the top consumers so the question is still answered.
        top = ranked[:3]
        listed = ", ".join(f"{u['user']} ({_fmt(_share(u) or 0)}%)" for u in top)
        flags.append({
            "type": "capacity.user-ranking",
            "resource": "top-users",
            "when": "",
            "evidence": {
                "topUsers": [{"user": u["user"],
                              "sharePct": round(_share(u) or 0, 1)} for u in top],
                "userCount": len(users),
            },
            "what": (f"No single user is over {_fmt(min_share)}% of monitored CU "
                     f"(load is spread across {len(users)} users). Top consumers: {listed}."),
        })

    return flags
