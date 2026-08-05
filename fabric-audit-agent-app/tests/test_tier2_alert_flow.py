"""Tier-2 alert orchestration — the dedup/48h/escalate/resolve state machine (injected fakes)."""
from datetime import datetime, timezone, timedelta

from fabric_audit_agent.automation.tier2_check import process_alerts
from fabric_audit_agent.automation.materiality import load_cfg
from fabric_audit_agent.context_alerts import create_alerts_store_memory

T0 = datetime(2026, 8, 4, 10, 0, 0, tzinfo=timezone.utc)


def _cfg_no_hysteresis():
    """Config with hysteresis disabled — for tests exercising absence/reopen in isolation, where
    the attribution incident must go active on the first run (hysteresis is covered separately)."""
    c = load_cfg()
    c["hysteresis_ticks"] = 1
    return c


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

    # run 3: +49h, still active -> SILENT, NO Teams re-post (the persistent surface is the
    # notification center now; recurring tickets must not be repeated in Teams).
    a = process_alerts([warn], now_dt=T0 + timedelta(hours=49), **kw)
    assert a["silent"] == ["pressure::capacity"] and rc["n"] == 1
    assert len(posts) == 1  # still just the one 'new' card — no reminder re-post

    # run 4: escalation (peak 130 -> 156) -> re-alert WITH a fresh LLM call (a genuine worsening
    # still breaks through — that is news, not repetition).
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


def test_active_incident_never_reposts_to_teams():
    """A still-firing open incident must NOT re-post to Teams on any later run — no matter how much
    time passes. Recurring tickets live in the app's notification center now; repeating them in
    Teams is exactly the noise we removed. Only ONE card (the original 'new') is ever sent."""
    store = create_alerts_store_memory()
    r, _ = _reasoner()
    w, _ = _writer()
    posts, sink = _sink()
    kw = dict(alerts_store=store, delivery_sinks={"webhook": sink}, reasoner=r, chat_writer=w,
              app_url="https://app")
    warn = {"check": "pressure", "peakCuPct": 130}

    process_alerts([warn], now_dt=T0, **kw)                             # run 1: new -> 1 card
    assert len(posts) == 1
    for hrs in (49, 98, 147, 300):                                      # long after any old 48h window
        a = process_alerts([warn], now_dt=T0 + timedelta(hours=hrs), **kw)
        assert a["silent"] == ["pressure::capacity"] and a["reminder"] == []
    assert len(posts) == 1                                              # still exactly one card, ever
    # the ticket is still open + active for the notification center
    assert store["query_active"]()["pressure::capacity"]["currentlyActive"] is True


def test_attribution_absence_does_not_resolve_or_card_but_capacity_does():
    """Step 2/8 anti-flapping: when an attribution incident's condition goes absent it must NOT send
    a Resolved card or auto-resolve (that + re-fire is the flap) — it just goes currentlyActive=False
    and stays open. A capacity incident still auto-resolves + cards (genuine physical state)."""
    import json
    store = create_alerts_store_memory()
    r, _ = _reasoner()
    w, _ = _writer()
    posts, sink = _sink()
    kw = dict(alerts_store=store, delivery_sinks={"webhook": sink}, reasoner=r, chat_writer=w,
              app_url="https://app", cfg=_cfg_no_hysteresis())
    xu = {"check": "cross_user", "item": "Sales", "workspace": "Fin", "userCount": 4,
          "users": ["a", "b", "c", "d"], "sharePct": 30}
    pr = {"check": "pressure", "peakCuPct": 130}
    process_alerts([xu, pr], now_dt=T0, **kw)
    assert set(store["query_active"]()) == {"cross_user::Fin/Sales", "pressure::capacity"}

    posts.clear()
    a = process_alerts([], now_dt=T0 + timedelta(minutes=5), **kw)   # neither fires now
    assert a["resolved"] == ["pressure::capacity"]          # capacity auto-resolves
    assert a["inactive"] == ["cross_user::Fin/Sales"]       # attribution just goes inactive
    active = store["query_active"]()
    assert "pressure::capacity" not in active               # capacity resolved (gone from active)
    assert active["cross_user::Fin/Sales"]["currentlyActive"] is False  # attribution still open
    resolved_cards = [p for p in posts if "Resolved" in json.dumps(p)]
    assert len(resolved_cards) == 1                          # ONLY the capacity resolved card


def test_resolved_incident_reopens_on_recurrence():
    """Step 8: a resolved attribution ticket that goes absent then RECURS reopens the same ticket
    (clears the resolve + re-alerts with a recurrence note) — not silently suppressed, not a new one."""
    import json
    store = create_alerts_store_memory()
    r, _ = _reasoner()
    w, _ = _writer()
    posts, sink = _sink()
    kw = dict(alerts_store=store, delivery_sinks={"webhook": sink}, reasoner=r, chat_writer=w,
              app_url="https://app", cfg=_cfg_no_hysteresis())
    xu = {"check": "cross_user", "item": "Sales", "workspace": "Fin", "userCount": 4,
          "users": ["a", "b", "c", "d"], "sharePct": 30}
    key = "cross_user::Fin/Sales"

    process_alerts([xu], now_dt=T0, **kw)                       # new active ticket
    cid = store["query_active"]()[key]["chatId"]
    process_alerts([], now_dt=T0 + timedelta(minutes=5), **kw)  # absent -> currentlyActive False
    assert store["query_active"]()[key]["currentlyActive"] is False

    reopened = {"n": 0}
    ack = {"get": lambda c: ({"status": "resolved", "resolutionNote": "fixed the query"}
                             if c == cid else None),
           "reopen": lambda c: reopened.__setitem__("n", reopened["n"] + 1)}
    posts.clear()
    a = process_alerts([xu], now_dt=T0 + timedelta(minutes=10), ack_store=ack, **kw)  # recurs
    assert a["reopened"] == [key]
    assert reopened["n"] == 1                                    # the resolve was cleared
    assert store["query_active"]()[key]["currentlyActive"] is True
    assert "Recurred" in json.dumps(posts[-1])                  # re-alert notes the recurrence
    # ticket memory (Step 7): the deep-link's auto-investigate query carries the prior note so the
    # investigation opens knowing this is a recurrence of a human-resolved ticket.
    _deeplink = _card(posts)["actions"][0]["url"]
    assert "RECURRED" in _deeplink and "fixed%20the%20query" in _deeplink


def test_ticket_writer_gets_detail_metadata_on_new_and_inactive():
    """Step 9: a new alert writes its descriptive metadata (chat_id -> what/where/severity/active)
    to the ticket store the app reads; when the condition goes absent, currentlyActive flips to
    False so the sidebar can show it's no longer firing."""
    store = create_alerts_store_memory()
    r, _ = _reasoner()
    w, _ = _writer()
    posts, sink = _sink()
    tickets = {}

    def ticket_writer(chat_id, meta):
        tickets[chat_id] = dict(meta)

    kw = dict(alerts_store=store, delivery_sinks={"webhook": sink}, reasoner=r, chat_writer=w,
              app_url="https://app", ticket_writer=ticket_writer, cfg=_cfg_no_hysteresis())
    xu = {"check": "cross_user", "item": "Sales", "workspace": "Fin", "userCount": 4,
          "users": ["a", "b", "c", "d"], "sharePct": 30}

    process_alerts([xu], now_dt=T0, **kw)
    cid = store["query_active"]()["cross_user::Fin/Sales"]["chatId"]
    assert cid in tickets
    m = tickets[cid]
    assert m["checkType"] == "cross_user" and m["resource"] == "Sales" and m["workspace"] == "Fin"
    assert m["severity"] == "warn" and m["currentlyActive"] is True
    assert m["incidentKey"] == "cross_user::Fin/Sales" and m["firstDetected"]

    # condition absent next run -> ticket flips to currentlyActive False (sidebar shows "inactive")
    # and workspace is preserved (derived from the incident key, since no trigger is present).
    process_alerts([], now_dt=T0 + timedelta(minutes=5), **kw)
    assert tickets[cid]["currentlyActive"] is False
    assert tickets[cid]["workspace"] == "Fin"


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

    # concentration 36 -> ambiguous; LLM says suppress -> silent, no card. Hysteresis disabled so
    # the ambiguous->LLM path is exercised on the first tick (otherwise the signal would sit in
    # pending for the persistence window before the reasoner is ever consulted).
    a = process_alerts([{"check": "concentration", "workspace": "W", "item": "I", "sharePct": 36}],
                       alerts_store=store, delivery_sinks={"webhook": sink},
                       reasoner=reasoner_suppress, now_dt=T0, cfg=_cfg_no_hysteresis())
    assert a["silent"] and posts == []


def test_hysteresis_holds_attribution_pending_until_it_persists():
    """Step 2.3 anti-flap: a fresh attribution signal must persist 3 consecutive 5-min checks
    (15 min) before it alerts. The first two ticks are 'pending' — no card, no chat, not active —
    and only the third promotes it to a real, delivered incident."""
    store = create_alerts_store_memory()
    r, rc = _reasoner()
    w, wc = _writer()
    posts, sink = _sink()
    kw = dict(alerts_store=store, delivery_sinks={"webhook": sink}, reasoner=r, chat_writer=w,
              app_url="https://app")  # default cfg -> hysteresis_ticks = 3
    xu = {"check": "cross_user", "item": "Sales", "workspace": "Fin", "userCount": 4,
          "users": ["a", "b", "c", "d"], "sharePct": 30}
    key = "cross_user::Fin/Sales"

    # tick 1 & 2: pending — nothing delivered, nothing active, no LLM/chat spend
    a = process_alerts([xu], now_dt=T0, **kw)
    assert a["pending"] == [key] and a["new"] == [] and posts == []
    assert store["query_active"]() == {} and rc["n"] == 0 and wc["n"] == 0
    assert store["query_pending"]()[key]["presenceCount"] == 1

    a = process_alerts([xu], now_dt=T0 + timedelta(minutes=5), **kw)
    assert a["pending"] == [key] and posts == []
    assert store["query_pending"]()[key]["presenceCount"] == 2

    # tick 3: sustained across the full window -> promoted to a real, delivered incident
    a = process_alerts([xu], now_dt=T0 + timedelta(minutes=10), **kw)
    assert a["new"] == [key] and len(posts) == 1
    assert set(store["query_active"]()) == {key}
    assert store["query_pending"]() == {}  # the pending row was consumed on promotion


def test_info_level_concentration_still_alerts_once_it_persists():
    """Regression guard for the "stopped getting alerts" outage: an Info-severity but MATERIAL
    concentration (share >= report threshold, but < the Warning cutoff) must still alert once it
    persists the hysteresis window. A prior build suppressed all Info attribution outright, which
    silenced the entire concentration / cross-user stream."""
    store = create_alerts_store_memory()
    r, _ = _reasoner()
    w, _ = _writer()
    posts, sink = _sink()
    kw = dict(alerts_store=store, delivery_sinks={"webhook": sink}, reasoner=r, chat_writer=w,
              app_url="https://app")  # default cfg -> hysteresis_ticks = 3
    # share 46% -> Info severity (warn cutoff is 50%) but >= the 40% report threshold -> material
    con = {"check": "concentration", "item": "Ent-Sales", "workspace": "Fin", "sharePct": 46}
    key = "concentration::Fin/Ent-Sales"

    a = process_alerts([con], now_dt=T0, **kw)
    assert a["pending"] == [key] and a["new"] == [] and posts == []      # held, not suppressed
    a = process_alerts([con], now_dt=T0 + timedelta(minutes=5), **kw)
    assert a["pending"] == [key] and posts == []
    a = process_alerts([con], now_dt=T0 + timedelta(minutes=10), **kw)   # 3rd consecutive -> promote
    assert a["new"] == [key] and len(posts) == 1
    assert set(store["query_active"]()) == {key}


def test_hysteresis_resets_when_the_signal_lapses():
    """A pending streak only counts CONSECUTIVE checks: if the signal is absent for a tick, its
    pending row is dropped and the count restarts from 1 — a flapping signal never promotes."""
    store = create_alerts_store_memory()
    r, _ = _reasoner()
    w, _ = _writer()
    posts, sink = _sink()
    kw = dict(alerts_store=store, delivery_sinks={"webhook": sink}, reasoner=r, chat_writer=w,
              app_url="https://app")  # default cfg -> hysteresis_ticks = 3
    xu = {"check": "cross_user", "item": "Sales", "workspace": "Fin", "userCount": 4,
          "users": ["a", "b", "c", "d"], "sharePct": 30}
    key = "cross_user::Fin/Sales"

    process_alerts([xu], now_dt=T0, **kw)                          # pending count 1
    process_alerts([xu], now_dt=T0 + timedelta(minutes=5), **kw)   # pending count 2
    assert store["query_pending"]()[key]["presenceCount"] == 2

    # gap: signal absent -> streak broken, pending row dropped
    process_alerts([], now_dt=T0 + timedelta(minutes=10), **kw)
    assert store["query_pending"]() == {}

    # signal returns -> starts over at 1, still NOT active (never reached 3 in a row)
    a = process_alerts([xu], now_dt=T0 + timedelta(minutes=15), **kw)
    assert a["pending"] == [key] and store["query_active"]() == {} and posts == []
    assert store["query_pending"]()[key]["presenceCount"] == 1
