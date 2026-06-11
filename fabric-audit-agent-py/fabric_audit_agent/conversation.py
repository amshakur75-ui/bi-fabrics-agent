"""Teams two-way conversation + concentration alerts (the 30% feature's UI surface).

NEW (no Node reference). Two pure, testable parts:
  - ``build_concentration_alert(finding)``: an outbound Teams card for a >=30% finding,
    leading **User -> Item -> Owner**, with quick-reply actions. Posted via
    ``adapters.delivery_teams``.
  - ``answer_question(text, envelope)``: inbound Q&A answered from the latest audit envelope
    (verdict / who's driving CU / top fixes / health). The host routes inbound Teams messages
    here and posts the reply back.

Deploy note (from the research): a **Databricks App cannot be the Bot Framework messaging
endpoint** — inbound Teams posts require the Bot Service OAuth handshake on the inbound call.
Front the bot with an Azure Bot Service / Function (or a Copilot Studio topic) that forwards
the user's text to ``answer_question()`` and posts the reply + alerts to the channel.
"""

_CONCENTRATION = "capacity.concentration"


def _driver_of(finding):
    """Short 'who' phrase for a concentration finding: named users, else owner, else pending."""
    ev = finding.get("evidence") or {}
    top = ev.get("topUsers")
    if isinstance(top, list) and top:
        return ", ".join(str(u.get("user")) for u in top)
    if ev.get("owner"):
        return f"owner/initiator {ev.get('owner')}"
    return "users pending activity-log correlation"


def build_concentration_alert(finding):
    """Outbound Teams card for a single >=30% concentration finding (User -> Item -> Owner)."""
    f = finding or {}
    ev = f.get("evidence") or {}
    key = f.get("key")
    contact = (ev.get("topUsers") or [{}])[0].get("user") or ev.get("owner")
    actions = [{"title": "Acknowledge", "value": f"ack {key}"},
               {"title": "Snooze 7d", "value": f"snooze {key} 7d"}]
    if contact:
        actions.append({"title": f"Contact {contact}", "value": f"contact {contact}"})
    return {
        "type": "message",
        "summary": "Capacity concentration alert",
        "sections": [{
            "heading": "⚠️ Capacity concentration",
            "text": f.get("what"),
            "facts": [
                {"name": "Share of CU", "value": f'{(ev.get("sharePct"))}%'},
                {"name": "Driver", "value": _driver_of(f)},
                {"name": "Mode", "value": ev.get("attributionMode") or "n/a"},
            ],
        }],
        "actions": actions,
    }


def _help():
    return ("I can answer: \"what's the verdict?\", \"who is driving capacity?\", "
            "\"what are the top fixes?\", \"what's the health score?\".")


def _who_summary(d):
    findings = d.get("findings") or []
    conc = [f for f in findings if str(f.get("key") or "").startswith(_CONCENTRATION)]
    if not conc:
        return "No single item is over the concentration threshold right now."
    # Each finding's `what` is already User-first; append a compact driver tag for clarity.
    return "\n".join(f'{f.get("what")} [driver: {_driver_of(f)}]' for f in conc)


def _top_fixes(d):
    roadmap = d.get("roadmap") or []
    if not roadmap:
        return "No fixes are queued — the estate looks healthy."
    return "Top fixes:\n" + "\n".join(
        f'#{r.get("rank")} [{r.get("level")}] {r.get("what")}' for r in roadmap[:3]
    )


def answer_question(text, envelope):
    """Answer a free-text question from the latest audit envelope. Pure."""
    d = (envelope or {}).get("data") or {}
    q = (text or "").strip().lower()
    if not q or "help" in q:
        return _help()
    if any(k in q for k in ("who", "user", "driv", "consum", "noisy", "30%", "hog")):
        return _who_summary(d)
    if any(k in q for k in ("verdict", "buy", "optimi", "size up", "size-up", "bigger", "sku")):
        v = d.get("verdict") or {}
        return f'Capacity verdict: {str(v.get("decision", "unknown")).upper()} — {v.get("reason", "(no reason given)")}'
    if any(k in q for k in ("top", "fix", "priorit", "roadmap", "next", "what should")):
        return _top_fixes(d)
    if any(k in q for k in ("health", "score")):
        hs = d.get("healthScore") or {}
        return f'Estate health: {hs.get("overall", "?")}/100.'
    return (envelope or {}).get("summary") or "No audit has run yet — ask me to run an audit."
