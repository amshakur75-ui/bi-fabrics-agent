"""Noisy-neighbor detector. Faithful port of the Node ``core/detectors/concentration.js``.

Flags any single item consuming >= threshold% of the capacity's CU (reads facts.items).
USER-FIRST: leads with the driving user(s) when activity-log attribution is attached,
names the owner for background-dominated load, else notes users are pending correlation.
"""
import math
from ..config import DEFAULT_CONFIG


def _fmt(x):
    """Render a share like JS template literals: 70.0 -> '70', 32.4 -> '32.4'."""
    return str(int(x)) if x == int(x) else str(x)


def detect_concentration(facts, config=None):
    config = config or DEFAULT_CONFIG
    items = (facts or {}).get("items") or []
    min_share = config["capacity"]["concentrationPct"]
    flags = []

    for it in items:
        try:
            share = float(it.get("sharePct"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(share) or share < min_share:
            continue
        share_out = int(share) if share == int(share) else share   # 70.0 -> 70 for clean JSON parity

        ws = it.get("workspace") or "unknown workspace"
        tu = it.get("topUsers")
        named = tu if isinstance(tu, list) and tu else None
        total_users = it.get("userCount")
        if total_users is None:
            total_users = it.get("users")
        if total_users is None and named:
            total_users = len(named)

        if named and it.get("background"):
            owner = it.get("owner") or named[0].get("user")
            what = (f"\"{it.get('name')}\" ({ws}) is using {_fmt(share)}% of capacity CU — "
                    f"driven mainly by background operations (owner/initiator: {owner}), not interactive users.")
        elif named:
            names = ", ".join(u.get("user") for u in named)
            more = max(0, total_users - len(named)) if total_users is not None else 0
            suffix = f" + {more} more" if more > 0 else ""
            what = f"{names}{suffix} are driving {_fmt(share)}% of capacity CU via \"{it.get('name')}\" ({ws})."
        else:
            who = f"{it.get('users')} user(s)" if it.get("users") else "unknown users"
            what = (f"\"{it.get('name')}\" ({ws}) is using {_fmt(share)}% of capacity CU across {who} "
                    f"— specific users pending activity-log correlation.")

        flags.append({
            "type": "capacity.concentration",
            "resource": f"{it.get('workspace') or '(unknown ws)'} / {it.get('name')}",
            "when": it.get("observedAt") or "",
            "evidence": {
                "sharePct": share_out, "cuSeconds": it.get("cuSeconds"), "kind": it.get("kind"),
                "users": it.get("users"), "userCount": it.get("userCount"), "topUsers": named,
                "background": it.get("background") or False, "owner": it.get("owner"),
                "attributionMode": it.get("attributionMode"),
            },
            "what": what,
        })
    return flags
