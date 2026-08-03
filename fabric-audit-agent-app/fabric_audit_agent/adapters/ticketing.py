"""Ticketing DeliveryPort: open tracked work items for findings via an injected client.

Port of ``adapters/ticketing.js``. Severity-gated + deduped. The injected ``client``
implements ``create_issue(ticket)``. At deploy this is a Jira / Azure DevOps / ServiceNow
client; in tests it's a fake that captures calls.
"""
from ..ticket import build_ticket

_LEVEL_RANK = {"Critical": 0, "Warning": 1, "Info": 2}


def create_ticketing_delivery(client, min_level="Critical"):
    floor = _LEVEL_RANK.get(min_level)
    if floor is None:
        raise ValueError(
            f'create_ticketing_delivery: unknown min_level "{min_level}". '
            f'Valid: {", ".join(_LEVEL_RANK)}'
        )

    def open_(findings=None, already_ticketed=None):
        findings = findings or []
        already_ticketed = already_ticketed if already_ticketed is not None else set()
        created = []
        for f in findings:
            level = (f.get("score") or {}).get("level")
            if _LEVEL_RANK.get(level, 9) > floor:   # below severity floor
                continue
            key = f.get("key")
            if key and key in already_ticketed:      # dedupe
                continue
            client.create_issue(build_ticket(f))
            if key:
                created.append(key)
        return {"created": created}

    return {"open": open_}
