"""Tier-2 alert orchestration — the dedup/48h/escalate/resolve state machine (injected fakes)."""
from datetime import datetime, timezone, timedelta

from fabric_audit_agent.automation.tier2_check import process_alerts
from fabric_audit_agent.context_alerts import create_alerts_store_memory

T0 = datetime(2026, 8, 4, 10, 0, 0, tzinfo=timezone.utc)


def _reasoner():
    calls = {"n": 0}

    def r(t):
        calls["n"] += 1
        return {"markdown": "# Investigation\nDetails.", "summary": "sustained", "report": True}
    return r, calls


def _writer():
    calls = {"n": 0}

    def w(md, title):
        calls["n"] += 1
        return f"chat-{calls['n']}"
    return w, calls


def _sink():
    posts = []
    return posts, {"deliver": lambda body: (posts.append(body),
                                            {"delivered": True, "status": 202})[1]}


def _card(posts):
    return posts[-1]["attachments"][0]["content"]


def test_full_state_machine():
    store = create_alerts_store_memory()
    r, rc = _reasoner()
    w, wc = _writer()
    posts, sink = _sink()
    sinks = {"webhook": sink}
    warn = {"check": "pressure", "peakCuPct": 130}  # derived warn -> report
    kw = dict(alerts_store=store, delivery_sinks=sinks, reasoner=r, chat_writer=w, app_url="https://app")

    # run 1: new incident -> 1 LLM, 1 chat, 1 card with deep-link
    a = process_alerts([warn], now_dt=T0, **kw)
    assert a["new"] == ["pressure::capacity"]
    assert rc["n"] == 1 and wc["n"] == 1 and len(posts) == 1
    _url = _card(posts)["actions"][0]["url"]
    assert _url.startswith("https://app/chat/chat-1?query=")  # deep-link auto-investigates on open
    row = store["query_active"]()["pressure::capacity"]
    assert row["chatId"] == "chat-1" and row["metric"] == 130.0

    # run 2: same, <48h, not escalated -> silent (NO LLM, NO card)
    a = process_alerts([warn], now_dt=T0 + timedelta(minutes=5), **kw)
    assert a["silent"] == ["pressure::capacity"] and rc["n"] == 1 and len(posts) == 1

    # run 3: +49h, still active -> reminder that REUSES the investigation (no LLM)
    a = process_alerts([warn], now_dt=T0 + timedelta(hours=49), **kw)
    assert a["reminder"] == ["pressure::capacity"] and rc["n"] == 1
    assert len(posts) == 2 and "Still active" in _card(posts)["body"][0]["text"]

    # run 4: escalation (peak 130 -> 156) -> re-alert WITH a fresh LLM call
    a = process_alerts([{"check": "pressure", "peakCuPct": 156}],
                       now_dt=T0 + timedelta(hours=50), **kw)
    assert a["escalation"] == ["pressure::capacity"] and rc["n"] == 2
    assert store["query_active"]()["pressure::capacity"]["escalationCount"] == 1

    # run 5: trigger gone -> resolved card + row resolved
    a = process_alerts([], now_dt=T0 + timedelta(hours=51), **kw)
    assert a["resolved"] == ["pressure::capacity"]
    assert store["query_active"]() == {}
    assert "Resolved" in _card(posts)["body"][0]["text"]


def test_chat_write_failure_falls_back_to_root_autoinvestigate_link():
    """If the chat write fails (returns None), the deep-link must NOT be a fake /chat/<uuid>
    (guaranteed 404). It falls back to the app root with ?query= — a fresh chat that
    auto-investigates on open, so the link is always present AND always resolves."""
    store = create_alerts_store_memory()
    r, _ = _reasoner()
    posts, sink = _sink()

    def failing_writer(md, title):
        return None  # simulate a DB write that produced no chat id

    a = process_alerts([{"check": "pressure", "peakCuPct": 130}], now_dt=T0,
                       alerts_store=store, delivery_sinks={"webhook": sink},
                       reasoner=r, chat_writer=failing_writer, app_url="https://app")
    assert a["new"] == ["pressure::capacity"]
    url = _card(posts)["actions"][0]["url"]
    assert url.startswith("https://app/?query=")  # root, auto-investigating
    assert "/chat/None" not in url and "/chat/" not in url
    # row carries no chatId (nothing real was written)
    assert store["query_active"]()["pressure::capacity"]["chatId"] is None


def test_clear_suppress_never_calls_llm_or_sends():
    store = create_alerts_store_memory()
    r, rc = _reasoner()
    posts, sink = _sink()
    trig = {"check": "concentration", "workspace": "W", "item": "I", "sharePct": 31}
    a = process_alerts([trig], alerts_store=store, delivery_sinks={"webhook": sink},
                       reasoner=r, now_dt=T0)
    assert a["silent"] == ["concentration::W/I"]
    assert rc["n"] == 0 and posts == []


def test_ambiguous_uses_llm_verdict():
    store = create_alerts_store_memory()
    posts, sink = _sink()

    def reasoner_suppress(t):
        return {"markdown": "m", "summary": "s", "report": False}

    # concentration 36 -> ambiguous; LLM says suppress -> silent, no card
    a = process_alerts([{"check": "concentration", "workspace": "W", "item": "I", "sharePct": 36}],
                       alerts_store=store, delivery_sinks={"webhook": sink},
                       reasoner=reasoner_suppress, now_dt=T0)
    assert a["silent"] and posts == []
