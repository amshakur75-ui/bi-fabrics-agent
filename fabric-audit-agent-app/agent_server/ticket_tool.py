"""Manual ticket creation — a local agent tool that lets a user flag something from a chat into the
shared notification center for the team, reusing the SAME infra as automated tickets (a public
``fabric-audit-agent`` chat + an ``alert_ticket`` row read by ``/api/alerts``). No parallel system.

The agent calls ``create_notification_ticket`` when the user asks to "put this in the notification
center / flag this for the team". The write goes through ``chat_store_lakebase`` (the app SP owns
``ai_chatbot``). Fail-open: any error returns ``{"created": False, "error": ...}`` so a failed write
never breaks the conversation — the agent just relays that it couldn't post it.
"""
import asyncio
import uuid
from datetime import datetime, timezone

_TITLE_MAX = 120
_DETAIL_MAX = 4000

_TOOL = {
    "name": "create_notification_ticket",
    "description": (
        "Create a ticket in the shared notification center so the team sees a specific issue to "
        "resolve or chat about. Use ONLY when the user explicitly asks to flag / save / post / add "
        "something for others to see (e.g. 'put this in the notification center', 'flag this for "
        "the team'). Capture the concrete finding and the conversation's conclusion as the detail — "
        "not a raw transcript. Read-only elsewhere; this is the one surface the user can write to."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short glanceable ticket title (<=120 chars)."},
            "detail": {"type": "string", "description": "The finding + conclusion + why it matters, "
                       "in the user's context. Self-contained so a teammate understands it without "
                       "opening the chat."},
            "resource": {"type": "string", "description": "Optional item/report/model the ticket is about."},
            "workspace": {"type": "string", "description": "Optional workspace name."},
        },
        "required": ["title", "detail"],
    },
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def create_ticket_tool_and_dispatch(*, chat_writer=None, ticket_writer=None):
    """Return ``([tool_def], {name: async handler})``. ``chat_writer(markdown, title)->chat_id`` and
    ``ticket_writer(chat_id, meta)`` are injectable for tests; in production they default to the
    Lakebase-backed ones from ``chat_store_lakebase``."""

    def _write(inp):
        title = (str(inp.get("title") or "").strip() or "Flagged from chat")[:_TITLE_MAX]
        detail = str(inp.get("detail") or "").strip()[:_DETAIL_MAX]
        resource = (inp.get("resource") or title)
        workspace = inp.get("workspace")
        markdown = (f"# {title}\n\n{detail}\n\n"
                    "_Flagged manually from a chat conversation — added to the notification center "
                    "for the team._")
        cw = chat_writer
        tw = ticket_writer
        if cw is None or tw is None:
            from fabric_audit_agent.adapters.chat_store_lakebase import (
                create_alert_chat, create_ticket_writer)
            cw = cw or create_alert_chat
            tw = tw or create_ticket_writer()
        chat_id = cw(markdown, title)
        if not chat_id:
            return {"created": False, "error": "could not create the ticket conversation"}
        tw(chat_id, {
            "incidentKey": f"manual::{chat_id}", "checkType": "manual", "severity": "info",
            "resource": resource, "workspace": workspace, "detail": detail[:500],
            "firstDetected": _now_iso(), "currentlyActive": True,
        })
        return {"created": True, "ticketId": chat_id,
                "message": "Added to the notification center for the team."}

    async def handler(inp):
        try:
            return await asyncio.to_thread(_write, inp or {})
        except Exception as exc:  # never break the chat over a failed write
            return {"created": False, "error": f"{type(exc).__name__}: {exc}"}

    return [_TOOL], {"create_notification_ticket": handler}
