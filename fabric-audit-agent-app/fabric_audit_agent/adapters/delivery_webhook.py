"""Tier-2 webhook delivery — Adaptive Card builder + a POST sink (Power Automate / Teams).

Cards are posted as ``{"attachments":[card]}`` (the flow's contract). Actual sends are routed
through ``outbound.dispatch_outbound`` (the egress chokepoint) — this module only builds cards and
POSTs. The webhook URL comes from a secret and is never logged.
"""
import json

_SEV_EMOJI = {"warn": "⚠️", "info": "ℹ️"}


def build_card(kind, *, title, severity="info", facts=None, summary=None, chat_url=None):
    """Build an Adaptive Card attachment. ``kind`` in ``new`` | ``reminder`` | ``resolved``."""
    if kind == "resolved":
        body = [{"type": "TextBlock", "text": f"✅ Resolved — {title}",
                 "weight": "Bolder", "size": "Medium", "wrap": True}]
    else:
        if kind == "new":
            head = f"{_SEV_EMOJI.get(severity, 'ℹ️')} {title}"
        else:  # reminder
            head = f"🔁 Still active — {title}"
        body = [{"type": "TextBlock", "text": head, "weight": "Bolder",
                 "size": "Medium", "wrap": True}]
        if facts:
            body.append({"type": "FactSet",
                         "facts": [{"title": str(n), "value": str(v)} for n, v in facts]})
        if summary:
            body.append({"type": "TextBlock", "text": summary, "wrap": True})
    content = {"type": "AdaptiveCard",
               "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
               "version": "1.2", "body": body}  # 1.2 for mobile Teams compatibility
    if chat_url and kind != "resolved":
        content["actions"] = [{"type": "Action.OpenUrl",
                               "title": "Investigate in chat", "url": chat_url}]
    return {"contentType": "application/vnd.microsoft.card.adaptive", "content": content}


def create_webhook_sink(url, *, poster=None):
    """Return a delivery sink ``{"deliver": fn(body) -> {"delivered","status"}}`` that POSTs JSON.

    ``poster(url, data_bytes) -> status_int`` is injectable for tests; the default uses urllib.
    """
    def _post(body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if poster is not None:
            return int(poster(url, data))
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return int(r.status)
        except urllib.error.HTTPError as e:
            return int(e.code)

    def deliver(body):
        status = _post(body)
        return {"delivered": 200 <= status < 300, "status": status}

    return {"deliver": deliver}
