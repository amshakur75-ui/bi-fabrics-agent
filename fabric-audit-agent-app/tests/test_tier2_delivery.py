"""Tier-2 webhook delivery — Adaptive Card builder + POST sink + outbound gating."""
import json

from fabric_audit_agent.adapters.delivery_webhook import build_card, create_webhook_sink
from fabric_audit_agent.outbound import dispatch_outbound


def test_build_new_card_has_facts_and_deeplink():
    card = build_card("new", title="Throttling on capacity", severity="warn",
                      facts=[("Throttle", "8 min"), ("Peak CU", "134%")],
                      summary="Sustained throttle during the 09:00 refresh window.",
                      chat_url="https://app/chat/abc")
    assert card["contentType"] == "application/vnd.microsoft.card.adaptive"
    c = card["content"]
    assert c["version"] == "1.4"
    kinds = [b["type"] for b in c["body"]]
    assert "FactSet" in kinds and kinds[0] == "TextBlock"
    assert c["actions"][0]["type"] == "Action.OpenUrl"
    assert c["actions"][0]["url"] == "https://app/chat/abc"
    assert "⚠️" in c["body"][0]["text"]


def test_reminder_and_resolved_cards():
    rem = build_card("reminder", title="Throttling", chat_url="https://app/chat/x")["content"]
    assert "Still active" in rem["body"][0]["text"] and rem["actions"]
    res = build_card("resolved", title="Throttling")["content"]
    assert "✅ Resolved" in res["body"][0]["text"]
    assert "actions" not in res  # resolved has no deep-link button


def test_webhook_sink_posts_utf8_attachments_and_reports_delivered():
    sent = {}

    def fake_poster(url, data):
        sent["url"] = url
        sent["body"] = json.loads(data.decode("utf-8"))
        return 202

    sink = create_webhook_sink("https://hook", poster=fake_poster)
    card = build_card("new", title="X", facts=[("A", "1")])
    out = sink["deliver"]({"attachments": [card]})
    assert out == {"delivered": True, "status": 202}
    assert sent["body"]["attachments"][0]["contentType"].endswith("card.adaptive")


def test_dispatch_routes_through_outbound_gate():
    posted = {}

    def poster(u, d):
        posted["d"] = d
        return 202

    sink = create_webhook_sink("https://hook", poster=poster)
    payload = {"attachments": [build_card("new", title="X")]}
    res = dispatch_outbound("tier2_alert", payload, sinks={"webhook": sink})
    assert res["dispatched"] is True and res["delivered"] is True
    # unknown/absent sink refuses without sending
    assert dispatch_outbound("tier2_alert", payload, sinks={})["dispatched"] is False
