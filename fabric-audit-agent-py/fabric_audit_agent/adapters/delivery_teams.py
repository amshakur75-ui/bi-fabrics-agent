"""Teams DeliveryPort. Port of ``adapters/delivery.teams.js``.

The HTTP client is injected (``http.post_json(url, body)``) so it's testable offline and
swaps to a real client (Azure Bot Service / incoming webhook) at deploy — see
``adapters.clients.EntraHttp``.
"""
from ..teams_card import build_teams_card, build_watch_adaptive_card


def create_teams_delivery(http, webhook_url):
    def deliver(envelope):
        card = build_teams_card(envelope)
        http.post_json(webhook_url, card)
        return {"delivered": True, "target": webhook_url, "sections": len(card["sections"])}

    return {"deliver": deliver}


def create_watch_delivery(http, webhook_url):
    """DeliveryPort for the autonomous watcher: POSTs a two-way Adaptive Card (Acknowledge /
    Snooze / Explain) per incident to the Power Automate Workflows webhook. ``http.post_json`` is
    injected (real client at deploy; a capture fake in tests). Returns a per-incident result so the
    orchestrator can record what was actually delivered (for dedup + audit)."""
    def deliver_incident(incident):
        card = build_watch_adaptive_card(incident)
        http.post_json(webhook_url, card)
        return {"delivered": True, "id": (incident or {}).get("id"),
                "severity": (incident or {}).get("severity")}

    return {"deliverIncident": deliver_incident}
