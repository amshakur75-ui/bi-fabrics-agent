"""Teams two-way conversation + concentration alerts (the 30% feature's UI surface).

NEW (no Node reference). Two pure, testable parts:
  - ``build_concentration_alert(item)``: an outbound Teams card leading **User -> Item ->
    Owner**, with quick-reply actions. Best called on a concentration **flag** from
    ``detectors.concentration`` (the immediate "someone just crossed 30%" path), which carries
    the structured ``evidence`` (topUsers/owner/sharePct). It also accepts a sweep **finding**
    (no ``evidence``) and degrades gracefully — the finding's ``what`` text already names the
    user, so the card stays correct (it just omits the structured facts/Contact it can't know).
  - ``answer_question(text, envelope)``: inbound Q&A answered from the latest audit envelope
    (verdict / who's driving CU / top fixes / health). The host routes inbound Teams messages
    here and posts the reply back.

Deploy note (from the research): a **Databricks App cannot be the Bot Framework messaging
endpoint** — inbound Teams posts require the Bot Service OAuth handshake on the inbound call.
Front the bot with an Azure Bot Service / Function (or a Copilot Studio topic) that forwards
the user's text to ``answer_question()`` and posts the reply + alerts to the channel.
"""
import re

_CONCENTRATION = "capacity.concentration"
_PERCENT_RE = re.compile(r"\d+\s*%")


def _driver_of(obj):
    """Named users, else owner — or None when no structured attribution is attached."""
    ev = obj.get("evidence") or {}
    top = ev.get("topUsers")
    if isinstance(top, list) and top:
        return ", ".join(str(u.get("user")) for u in top)
    if ev.get("owner"):
        return f"owner/initiator {ev.get('owner')}"
    return None


def _contact_of(obj):
    ev = obj.get("evidence") or {}
    top = ev.get("topUsers")
    if isinstance(top, list) and top and top[0].get("user"):
        return top[0].get("user")
    return ev.get("owner")


def build_concentration_alert(item):
    """Outbound Teams card for one >=30% item (flag or finding). User -> Item -> Owner."""
    item = item or {}
    ev = item.get("evidence") or {}
    key = item.get("key") or item.get("resource")
    text = item.get("what") or "An item is consuming a large share of capacity CU."

    facts = []
    if ev.get("sharePct") is not None:
        facts.append({"name": "Share of CU", "value": f'{ev.get("sharePct")}%'})
    driver = _driver_of(item)
    if driver:
        facts.append({"name": "Driver", "value": driver})
    if ev.get("attributionMode"):
        facts.append({"name": "Mode", "value": ev.get("attributionMode")})

    actions = [{"title": "Acknowledge", "value": f"ack {key}"},
               {"title": "Snooze 7d", "value": f"snooze {key} 7d"}]
    contact = _contact_of(item)
    if contact:
        actions.append({"title": f"Contact {contact}", "value": f"contact {contact}"})

    return {
        "type": "message",
        "summary": "Capacity concentration alert",
        "sections": [{"heading": "⚠️ Capacity concentration", "text": text, "facts": facts}],
        "actions": actions,
    }


def _help():
    return ("I can answer: \"what's the verdict?\", \"who is driving capacity?\", "
            "\"what are the top fixes?\", \"what's the health score?\".")


def _who_summary(d):
    conc = [f for f in (d.get("findings") or []) if str(f.get("key") or "").startswith(_CONCENTRATION)]
    if not conc:
        return "No single item is over the concentration threshold right now."
    # Each finding's `what` is already User-first; append a driver tag when structured data exists.
    lines = []
    for f in conc:
        what = f.get("what") or "(no description)"
        driver = _driver_of(f)
        lines.append(f"{what} [driver: {driver}]" if driver else what)
    return "\n".join(lines)


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
    if any(k in q for k in ("who", "user", "driv", "consum", "noisy", "hog")) or _PERCENT_RE.search(q):
        return _who_summary(d)
    if any(k in q for k in ("verdict", "buy", "optimi", "size", "oversiz", "bigger", "sku")):
        v = d.get("verdict") or {}
        return f'Capacity verdict: {str(v.get("decision", "unknown")).upper()} — {v.get("reason", "(no reason given)")}'
    if any(k in q for k in ("top", "fix", "priorit", "roadmap", "next", "what should")):
        return _top_fixes(d)
    if any(k in q for k in ("health", "score")):
        hs = d.get("healthScore") or {}
        return f'Estate health: {hs.get("overall", "?")}/100.'
    return (envelope or {}).get("summary") or "No audit has run yet — ask me to run an audit."
