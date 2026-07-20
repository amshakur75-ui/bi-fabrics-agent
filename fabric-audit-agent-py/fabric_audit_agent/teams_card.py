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


def build_watch_adaptive_card(incident):
    """Build the two-way Adaptive Card (message envelope) for a single watcher incident.

    ``incident``: {emoji, title, summary, why, severity, facts:[{title,value}], id,
    whenDisplay?}. Returns the ``type:"message"`` + ``attachments[]`` payload POSTed to the
    Workflows webhook.
    """
    inc = incident or {}
    color = _SEVERITY_COLOR.get(inc.get("severity"), "Default")
    body = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "wrap": True,
         "color": color, "text": _js_str(inc.get("title") or inc.get("summary"))},
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
    body.append({
        "type": "Input.ChoiceSet", "id": "response", "label": "What do you want to do?",
        "value": "acknowledge",
        "choices": [
            {"title": "Acknowledge", "value": "acknowledge"},
            {"title": "Snooze 1 hour", "value": "snooze"},
            {"title": "Explain in more detail", "value": "explain"},
        ],
    })
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard", "version": "1.4", "body": body,
        "actions": [{"type": "Action.Submit", "title": "Submit",
                     "data": {"incidentId": _js_str(inc.get("id")),
                              "incidentKind": _js_str(inc.get("kind"))}}],
    }
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": card,
        }],
    }
