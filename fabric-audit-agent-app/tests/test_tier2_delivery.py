"""Tier-2 webhook delivery — Adaptive Card builder + POST sink + outbound gating."""
import json
import urllib.error
import urllib.request

from fabric_audit_agent.adapters.delivery_webhook import (
    build_card, create_webhook_sink, PROXY_RANKING_DISCLOSURE)
from fabric_audit_agent.outbound import dispatch_outbound


def test_concentration_card_carries_proxy_disclosure_and_never_says_base_capacity():
    """Step 3 regression: a concentration/attribution card must state — verbatim — that the share
    is a monitored-activity PROXY, and must never mislabel it as '% of base capacity'."""
    card = build_card("new", title="Concentration: Sales at 42%", severity="warn",
                      facts=[("Item", "Sales"), ("Share", "42%")],
                      summary="One user drove 42% of monitored activity on Sales.",
                      disclosure=PROXY_RANKING_DISCLOSURE)
    blob = json.dumps(card)
    assert PROXY_RANKING_DISCLOSURE in blob            # disclosure present verbatim
    assert "base capacity" not in blob.lower()          # never mislabels the proxy as base capacity


def test_throttle_card_has_no_proxy_disclosure():
    """True-CU alerts (throttle/pressure) are NOT proxy — no disclosure line."""
    card = build_card("new", title="Throttling on capacity", facts=[("Peak CU", "134%")])
    assert PROXY_RANKING_DISCLOSURE not in json.dumps(card)


def test_build_new_card_has_facts_and_deeplink():
    card = build_card("new", title="Throttling on capacity", severity="warn",
                      facts=[("Throttle", "8 min"), ("Peak CU", "134%")],
                      summary="Sustained throttle during the 09:00 refresh window.",
                      chat_url="https://app/chat/abc")
    assert card["contentType"] == "application/vnd.microsoft.card.adaptive"
    c = card["content"]
    assert c["version"] == "1.2"  # mobile Teams compatibility
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


def test_webhook_sink_url_error_never_raises_and_reports_not_delivered(monkeypatch):
    """16c regression: a connection-level failure (DNS, refused connection, socket timeout — all
    urllib.error.URLError, HTTPError's parent, not itself an HTTP response) must be caught, not
    propagate as an unhandled exception, and must still yield the standard delivered=False shape."""
    def _raise_url_error(req, timeout=None):
        raise urllib.error.URLError("[Errno 11001] getaddrinfo failed")

    monkeypatch.setattr(urllib.request, "urlopen", _raise_url_error)

    sink = create_webhook_sink("https://hook.invalid")
    card = build_card("new", title="X", facts=[("A", "1")])
    out = sink["deliver"]({"attachments": [card]})
    assert out == {"delivered": False, "status": 0}


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
