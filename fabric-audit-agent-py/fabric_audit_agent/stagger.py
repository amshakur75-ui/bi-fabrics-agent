"""Propose a staggered refresh schedule for colliding times. Port of ``core/stagger.js``. Pure."""


def _add_minutes(hhmm, mins):
    parts = str(hhmm).split(":")
    h, m = int(parts[0]), int(parts[1])
    total = ((h * 60 + m + mins) % 1440 + 1440) % 1440
    return f"{total // 60:02d}:{total % 60:02d}"


def plan_stagger(facts=None, spacing_min=15, min_group=2):
    facts = facts or {}
    refreshes = (facts.get("capacity") or {}).get("refreshes") or []
    by_time = {}
    for r in refreshes:
        by_time.setdefault(r.get("scheduledAt"), []).append(r)

    plan = []
    for time, group in by_time.items():
        if len(group) < min_group:
            continue
        ordered = sorted(group, key=lambda r: -(r.get("sizeGB") or 0))   # largest first keeps its slot
        for i, r in enumerate(ordered):
            to = _add_minutes(time, i * spacing_min)
            if to != r.get("scheduledAt"):
                plan.append({"dataset": r.get("dataset"), "workspace": r.get("workspace"), "from": r.get("scheduledAt"), "to": to})
    return plan
