"""Step 6a — the hourly sweep delivers only NEW material findings, deduped vs the shared alerts store,
and never repeats a family Tier-2 already owns."""
import json

from fabric_audit_agent.automation.sweep_delivery import deliver_new_findings
from fabric_audit_agent.context_alerts import create_alerts_store_memory


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
