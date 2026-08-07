"""Step 6a — the hourly sweep delivers only NEW material findings, deduped vs the shared alerts store,
and never repeats a family Tier-2 already owns."""
import json

from fabric_audit_agent.automation.sweep_delivery import deliver_new_findings, short_title
from fabric_audit_agent.context_alerts import create_alerts_store_memory


def test_short_title_compresses_the_concentration_pattern():
    # the dominant alert phrasing collapses to a glanceable "<user> — <pct> of capacity"
    long = ("Ana.Gonzales@newellco.com is driving ~35.4% of capacity CU (est.) — "
            "mostly via \"Ent-Reporting-SCM\".")
    assert short_title(long) == "Ana.Gonzales — 35.4% of capacity"


def test_short_title_shortens_a_generic_long_sentence():
    t = short_title("The Finance workspace refresh has failed 9 times in a row since Tuesday morning")
    assert t.endswith("…") and len(t) <= 62 and t.startswith("The Finance")
    assert short_title("**Capacity is healthy today").startswith("Capacity")   # leading ** stripped
    assert short_title("") == "Finding"


def _f(key, level, what="something happened", where="Fin"):
    return {"key": key, "score": {"level": level}, "what": what, "where": where}


def _sink():
    posts = []
    return posts, {"deliver": lambda b: (posts.append(b), {"delivered": True, "status": 202})[1]}


def test_delivers_new_material_non_tier2_findings_only():
    store = create_alerts_store_memory()
    posts, sink = _sink()
    findings = [
        _f("model.bidirectional::Sales", "Warning"),      # delivered
        _f("capacity.throttle::cap", "Critical"),          # skipped (tier2 owns)
        _f("cost.idle_report::R1", "Info"),                # skipped (below Warning)
        _f("refresh.contention::WS", "Critical"),          # delivered
    ]
    out = deliver_new_findings(findings, alerts_store=store, delivery_sinks={"webhook": sink},
                               app_url="https://app")
    assert set(out["delivered"]) == {"model.bidirectional::Sales", "refresh.contention::WS"}
    assert out["skipped_tier2"] == 1 and out["skipped_minor"] == 1
    assert len(posts) == 2
    # each delivered finding becomes an active incident (checkType = the finding family) + a deep-link
    active = store["query_active"]()
    assert "refresh.contention::WS" in active and active["refresh.contention::WS"]["checkType"] == "refresh"
    assert active["model.bidirectional::Sales"]["checkType"] == "model"
    assert "/?query=" in json.dumps(posts[0]) or "/chat/" in json.dumps(posts[0])


def test_sweep_findings_write_notification_center_tickets():
    """The estate-wide sweep finding must also become an app notification-center TICKET (alert_ticket
    row, checkType = family), not only a Teams card — otherwise it's invisible in the app."""
    store = create_alerts_store_memory()
    posts, sink = _sink()
    tickets = {}

    def ticket_writer(chat_id, meta):
        tickets[chat_id] = dict(meta)

    out = deliver_new_findings([_f("model.bidirectional::Sales Model", "Warning",
                                   what="Model has 6 bidirectional relationships", where="Finance / Sales")],
                               alerts_store=store, delivery_sinks={"webhook": sink},
                               app_url="https://app", chat_writer=lambda m, t: "chat-7",
                               ticket_writer=ticket_writer)
    assert out["delivered"] == ["model.bidirectional::Sales Model"]
    assert "chat-7" in tickets
    m = tickets["chat-7"]
    assert m["checkType"] == "model" and m["currentlyActive"] is True
    assert m["resource"] == "Finance / Sales" and "bidirectional" in m["detail"]


def test_ticket_still_written_when_chat_creation_fails():
    """Part 7 (tightening.md): if chat_writer RAISES, chat_id stays None — but the finding's ticket
    must STILL be written (keyed by incidentKey) so it shows in the notification center. Before the
    fix this was gated ``if ticket_writer and chat_id`` and silently skipped every time chat write
    failed, even though the finding was "delivered" and the daily digest count looked correct."""
    store = create_alerts_store_memory()
    posts, sink = _sink()
    tickets = {}

    def failing_chat_writer(md, title):
        raise RuntimeError("lakebase unavailable")

    def ticket_writer(chat_id, meta):
        tickets[meta["incidentKey"]] = (chat_id, dict(meta))

    out = deliver_new_findings([_f("model.bidirectional::Sales Model", "Warning",
                                   what="Model has 6 bidirectional relationships", where="Finance / Sales")],
                               alerts_store=store, delivery_sinks={"webhook": sink},
                               app_url="https://app", chat_writer=failing_chat_writer,
                               ticket_writer=ticket_writer)
    # the finding is still delivered (not dropped) even though the chat write blew up
    assert out["delivered"] == ["model.bidirectional::Sales Model"]
    # the ticket landed, keyed by the stable incidentKey, with chat_id=None
    assert "model.bidirectional::Sales Model" in tickets
    cid, m = tickets["model.bidirectional::Sales Model"]
    assert cid is None
    assert m["checkType"] == "model" and m["currentlyActive"] is True
    assert m["resource"] == "Finance / Sales"
    # the deep-link degrades to the root ?query= auto-investigation link, never dropped
    blob = json.dumps(posts[0])
    assert "/?query=" in blob and "/chat/" not in blob


def test_ticket_still_written_when_chat_writer_returns_none():
    """Same failure mode, different shape: chat_writer returns None instead of raising (no chat
    write configured / a soft failure) — the ticket must still be written."""
    store = create_alerts_store_memory()
    posts, sink = _sink()
    tickets = {}

    def ticket_writer(chat_id, meta):
        tickets[meta["incidentKey"]] = chat_id

    out = deliver_new_findings([_f("refresh.contention::WS", "Critical")],
                               alerts_store=store, delivery_sinks={"webhook": sink},
                               app_url="https://app", chat_writer=lambda m, t: None,
                               ticket_writer=ticket_writer)
    assert out["delivered"] == ["refresh.contention::WS"]
    assert tickets.get("refresh.contention::WS") is None
    assert "refresh.contention::WS" in tickets


def test_user_ranking_finding_delivered_as_actionable_concentration_checktype():
    # a "capacity.user-ranking" key (the top-consumers info flag) surfaces as the actionable
    # 'concentration' checkType (not the bare 'capacity' family the notification center filters out)
    store = create_alerts_store_memory()
    posts, sink = _sink()
    f = [_f("capacity.user-ranking::top-users", "Warning", what="No single user is over 30%...",
            where="top-users")]
    out = deliver_new_findings(f, alerts_store=store, delivery_sinks={"webhook": sink},
                               app_url="https://app", chat_writer=lambda m, t: "c1")
    assert out["delivered"] == ["capacity.user-ranking::top-users"]
    assert store["query_active"]()["capacity.user-ranking::top-users"]["checkType"] == "concentration"


def test_investigate_query_anchors_to_the_fire_time():
    import urllib.parse
    store = create_alerts_store_memory()
    posts, sink = _sink()
    f = [_f("model.bidirectional::Sales", "Warning", what="6 bidirectional relationships")]
    deliver_new_findings(f, alerts_store=store, delivery_sinks={"webhook": sink}, app_url="https://app",
                         chat_writer=lambda m, t: "c9", now_iso="2026-08-06T03:17:00Z")
    blob = urllib.parse.unquote(json.dumps(posts[0]))
    assert "2026-08-06T03:17:00Z" in blob and "TIME WINDOW" in blob


def test_dedups_against_already_active_incidents():
    # a finding tier2 (or a prior sweep) already alerted -> not repeated
    store = create_alerts_store_memory({"model.autodate::M1": {"incidentKey": "model.autodate::M1",
                                                               "status": "active"}})
    posts, sink = _sink()
    out = deliver_new_findings([_f("model.autodate::M1", "Warning")],
                               alerts_store=store, delivery_sinks={"webhook": sink}, app_url="https://app")
    assert out["delivered"] == [] and out["skipped_dup"] == 1 and posts == []


def test_second_sweep_does_not_repeat_what_the_first_delivered():
    store = create_alerts_store_memory()
    _, sink = _sink()
    f = [_f("model.bidirectional::Sales", "Warning")]
    first = deliver_new_findings(f, alerts_store=store, delivery_sinks={"webhook": sink}, app_url="a")
    second = deliver_new_findings(f, alerts_store=store, delivery_sinks={"webhook": sink}, app_url="a")
    assert first["delivered"] == ["model.bidirectional::Sales"] and second["delivered"] == []
