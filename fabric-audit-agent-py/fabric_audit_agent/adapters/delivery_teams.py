"""Teams DeliveryPort. Port of ``adapters/delivery.teams.js``.

The HTTP client is injected (``http.post_json(url, body)``) so it's testable offline and
swaps to a real client (Azure Bot Service / incoming webhook) at deploy — see
``adapters.clients.EntraHttp``.
"""
from ..teams_card import build_teams_card


def create_teams_delivery(http, webhook_url):
    def deliver(envelope):
        card = build_teams_card(envelope)
        http.post_json(webhook_url, card)
        return {"delivered": True, "target": webhook_url, "sections": len(card["sections"])}

    return {"deliver": deliver}
