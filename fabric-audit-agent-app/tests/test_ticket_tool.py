"""Manual ticket creation tool — creates a chat + alert_ticket row via injected writers, fail-open."""
import asyncio
from agent_server.ticket_tool import create_ticket_tool_and_dispatch


def test_manual_ticket_creates_chat_and_ticket():
    chats, tickets = [], []

    def cw(md, title):
        chats.append((md, title))
        return "chat-9"

    def tw(cid, meta):
        tickets.append((cid, meta))

    tools, dispatch = create_ticket_tool_and_dispatch(chat_writer=cw, ticket_writer=tw)
    assert tools[0]["name"] == "create_notification_ticket"
    res = asyncio.run(dispatch["create_notification_ticket"]({
        "title": "SCM concentration worth watching",
        "detail": "Matthew drives 96% of DTC — a steady cadence, not the throttle driver.",
        "resource": "Ent-Reporting-DTC", "workspace": "Enterprise DTC"}))
    assert res["created"] is True and res["ticketId"] == "chat-9"
    assert chats and "Matthew" in chats[0][0] and chats[0][1].startswith("SCM concentration")
    cid, meta = tickets[0]
    assert cid == "chat-9"
    assert meta["checkType"] == "manual" and meta["currentlyActive"] is True
    assert meta["resource"] == "Ent-Reporting-DTC" and meta["workspace"] == "Enterprise DTC"
    assert "Matthew" in meta["detail"] and meta["incidentKey"] == "manual::chat-9"


def test_manual_ticket_fail_open_on_error():
    def cw(md, title):
        raise RuntimeError("lakebase unavailable")

    _, dispatch = create_ticket_tool_and_dispatch(chat_writer=cw, ticket_writer=lambda c, m: None)
    res = asyncio.run(dispatch["create_notification_ticket"]({"title": "x", "detail": "y"}))
    assert res["created"] is False and "error" in res   # never raises into the chat


def test_manual_ticket_handles_missing_chat_id():
    _, dispatch = create_ticket_tool_and_dispatch(
        chat_writer=lambda m, t: None, ticket_writer=lambda c, m: None)
    res = asyncio.run(dispatch["create_notification_ticket"]({"title": "x", "detail": "y"}))
    assert res["created"] is False
