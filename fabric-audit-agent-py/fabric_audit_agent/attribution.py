"""User-attribution engine. Faithful port of the Node ``core/attribution.js``.

Decides which user(s) are driving an item's CU consumption from that item's activity
records in the window. Pure: no I/O.

Ranking — "both if available": if any event carries a cost proxy (``cpuMs`` or
``durationMs`` from Log Analytics), rank users by summed cost ("cost" mode); otherwise
rank by operation count ("frequency" mode, e.g. Activity Events only).

Interactive vs background: ``background`` is computed COST-weighted in cost mode, so a
single heavy refresh (high cost, low op-count) is still flagged background -> the
owner/initiator is named instead of an interactive "consumer".
"""

import math

DEFAULT_TOP_N = 3


def _is_num(v):
    # mirrors JS Number.isFinite: rejects bool, NaN, and Infinity
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def attribute_users(events=None, *, top_n=DEFAULT_TOP_N, owner=None):
    """Rank the users driving one item's CU from its windowed events.

    :returns: ``{mode, userCount, background, owner, topUsers:[{user, ops, cpuMs, interactive, score}]}``
    """
    events = events or []
    has_cost = any(_is_num(e.get("cpuMs")) or _is_num(e.get("durationMs")) for e in events)
    mode = "cost" if has_cost else "frequency"

    by = {}
    bg_contribution = 0.0
    total_contribution = 0.0
    for e in events:
        user = str(e.get("user") or "").strip()
        if not user:
            continue
        cpu, dur = e.get("cpuMs"), e.get("durationMs")
        cost = cpu if _is_num(cpu) else (dur if _is_num(dur) else 0)
        contribution = cost if mode == "cost" else 1
        total_contribution += contribution
        if not e.get("interactive"):
            bg_contribution += contribution
        cur = by.get(user) or {"user": user, "ops": 0, "cpuMs": 0, "interactiveOps": 0}
        cur["ops"] += 1
        cur["cpuMs"] += cost
        if e.get("interactive"):
            cur["interactiveOps"] += 1
        by[user] = cur

    users = [
        {
            "user": u["user"], "ops": u["ops"], "cpuMs": int(math.floor(u["cpuMs"] + 0.5)),  # JS Math.round (half-up); cpuMs >= 0
            "interactive": u["interactiveOps"] > 0,
            "score": u["cpuMs"] if mode == "cost" else u["ops"],
        }
        for u in by.values()
    ]
    # rank by score, then ops, then name (stable + deterministic)
    users.sort(key=lambda u: (-u["score"], -u["ops"], u["user"]))

    background = total_contribution > 0 and (bg_contribution / total_contribution) >= 0.5

    return {
        "mode": mode,
        "userCount": len(by),
        "background": background,
        "owner": owner,
        "topUsers": users[:top_n],
    }


def enrich_items(items=None, events_by_item=None, *, top_n=DEFAULT_TOP_N, owner=None):
    """Attach attribution to each item that has events; leave the rest untouched.

    Sets ``topUsers / userCount / background / owner / attributionMode`` on matched items.
    ``events_by_item`` is keyed by item name (or id).
    """
    items = items or []
    events_by_item = events_by_item or {}
    out = []
    for it in items:
        events = events_by_item.get(it.get("name")) or events_by_item.get(it.get("id")) or []
        if not events:
            out.append(it)
            continue
        item_owner = it.get("owner") if it.get("owner") is not None else owner  # nullish, not falsy
        a = attribute_users(events, top_n=top_n, owner=item_owner)
        out.append({
            **it,
            "topUsers": a["topUsers"],
            "userCount": a["userCount"],
            "background": a["background"],
            "owner": a["owner"] if a["owner"] is not None else it.get("owner"),
            "attributionMode": a["mode"],
        })
    return out
