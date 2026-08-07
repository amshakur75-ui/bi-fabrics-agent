"""Ticketing DeliveryPort: open tracked work items for findings via an injected client.

Port of ``adapters/ticketing.js``. Severity-gated + deduped. The injected ``client``
implements ``create_issue(ticket)``. At deploy this is a Jira / Azure DevOps / ServiceNow
client; in tests it's a fake that captures calls.

Egress: the findings list is routed through the egress chokepoint
(``egress.apply_egress_controls(findings, sink="ticketing")``) before any ticket is built/sent —
see ``egress.py``'s module contract. A ticket client is a direct injected port (not a
``{"deliver": fn}`` sink), so this goes through ``apply_egress_controls`` directly rather than
``outbound.dispatch_outbound`` (which is for the sink-dict/allowlisted-action-type shape used by
tier2/daily-summary webhook delivery).
"""
from ..egress import apply_egress_controls
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
        safe_findings, _meta = apply_egress_controls(findings, sink="ticketing")
        created = []
        for f in safe_findings:
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
