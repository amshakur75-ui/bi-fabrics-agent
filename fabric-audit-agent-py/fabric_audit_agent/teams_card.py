"""Format an audit envelope into a Teams-style card payload. Port of ``core/teams-card.js``.

Representative shape; the exact Adaptive Card schema is finalized at deployment. Pure.
"""


def _js_str(v):
    """Mirror JS ``String(x)`` for the missing-value case: a missing dict key is the
    analog of JS ``undefined``, so None → ``"undefined"`` (Node renders the literal text
    and never throws), not Python's ``"None"``."""
    return "undefined" if v is None else str(v)


def _fix0(f):
    fix = f.get("fix")
    val = fix[0] if isinstance(fix, list) and fix else None
    return val if val is not None else "see report"


def build_teams_card(envelope):
    envelope = envelope or {}
    d = envelope.get("data") or {}
    findings = d.get("findings") or []
    criticals = [f for f in findings if (f.get("score") or {}).get("level") == "Critical"]
    verdict = d.get("verdict")
    summary = envelope.get("summary")

    sections = [{"heading": "Summary", "text": summary if summary is not None else ""}]
    if verdict:
        sections.append({"heading": "Capacity verdict", "text": f"{_js_str(verdict.get('decision')).upper()} — {_js_str(verdict.get('reason'))}"})
    sections.append({
        "heading": f"Critical findings ({len(criticals)})",
        "items": [f"{_js_str(f.get('what'))} — Fix: {_fix0(f)}" for f in criticals[:10]],
    })
    return {"type": "message", "summary": summary if summary is not None else "Fabric audit", "sections": sections}


# ---------------------------------------------------------------------------
# Two-way Adaptive Card for the autonomous watcher (Power Automate "Post adaptive
# card and wait for a response"). Shape: {type:"message", attachments:[{contentType:
# "application/vnd.microsoft.card.adaptive", content:<AdaptiveCard>}]}. The card carries an
# Input.ChoiceSet (Acknowledge / Snooze / Explain) + one Action.Submit -> the flow captures
# who submitted and their choice and can call back to a Databricks Job trigger URL. Pure.
# ---------------------------------------------------------------------------

_SEVERITY_COLOR = {"warn": "Attention", "info": "Good"}


def _context_deeplink(app_base_url, context):
    """Encode the incident ``context`` dict as urlsafe-base64 and append as ``?context=`` to the
    chat app URL. The React app decodes it and auto-fires the opening investigation question, so
    'Yes, show me more' lands the user on a live conversation. Returns None if either is missing."""
    if not app_base_url or context is None:
        return None
    import base64
    import json
    enc = base64.urlsafe_b64encode(
        json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    sep = "&" if "?" in app_base_url else "?"
    return f"{app_base_url}{sep}context={enc}"


def build_watch_adaptive_card(incident, *, app_base_url=None):
    """Build the alert Adaptive Card (message envelope) for one watcher incident.

    UX (approved 2026-07-19): a friendly heading, the finding + why, then two actions --
    **"Yes, show me more →"** (Action.OpenUrl deep-linking into the chat app with the encoded
    incident context, so the conversation is already running) and **"No, dismiss"** (Action.Submit
    with ``{response:"no"}``, which the Power Automate flow answers with "Dismissed..."). Shape is
    ``type:"message"`` + ``attachments[]`` for "Post adaptive card and wait for a response".

    ``incident``: {emoji, title, summary, why, severity, facts:[{title,value}], id, kind,
    whenDisplay?, context?}. ``app_base_url``: the chat app root (e.g.
    https://fabric-audit-agent-....azuredatabricksapps.com) -- when present with a context, the
    Yes button deep-links; otherwise Yes is omitted (No-only card).
    """
    inc = incident or {}
    color = _SEVERITY_COLOR.get(inc.get("severity"), "Default")
    body = [
        {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "wrap": True,
         "text": "👋 Just flagged something worth your attention."},
        {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "wrap": True,
         "color": color, "text": _js_str(inc.get("summary") or inc.get("title"))},
    ]
    if inc.get("whenDisplay"):
        body.append({"type": "TextBlock", "spacing": "None", "isSubtle": True, "wrap": True,
                     "text": _js_str(inc.get("whenDisplay"))})
    facts = [{"title": _js_str(f.get("title")), "value": _js_str(f.get("value"))}
             for f in (inc.get("facts") or [])]
    if facts:
        body.append({"type": "FactSet", "facts": facts})
    if inc.get("why"):
        body.append({"type": "TextBlock", "wrap": True, "text": _js_str(inc.get("why"))})
    body.append({"type": "TextBlock", "wrap": True, "weight": "Bolder", "spacing": "Medium",
                 "text": "Would you like to explore or dig into this more?"})

    actions = []
    deeplink = _context_deeplink(app_base_url, inc.get("context"))
    if deeplink:
        actions.append({"type": "Action.OpenUrl", "title": "Yes, let's dig in →", "url": deeplink})
    actions.append({"type": "Action.Submit", "title": "No, dismiss",
                    "data": {"response": "no", "incidentId": _js_str(inc.get("id"))}})

    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard", "version": "1.4", "body": body, "actions": actions,
    }
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": card,
        }],
    }
