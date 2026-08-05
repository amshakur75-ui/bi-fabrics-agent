"""Step 10 — daily capacity digest: pure card builder + the delivery/reconcile orchestrator."""
import json
from datetime import datetime, timezone

from fabric_audit_agent.automation.daily_summary import (
    build_daily_summary, run_daily_summary, digest_key,
)
from fabric_audit_agent.context_alerts import create_alerts_store_memory

NOW = datetime(2026, 8, 5, 18, 0, 0, tzinfo=timezone.utc)


def _sink():
    posts = []
    return posts, {"deliver": lambda b: (posts.append(b), {"delivered": True, "status": 202})[1]}


# ---- pure builder ----

def test_build_all_clear_digest_has_ack_action():
    md, card, summary = build_daily_summary(open_tickets=[], capacity={}, coverage_gaps=[],
                                            date_str="2026-08-05", app_url="https://app")
    assert "all clear" in md.lower()
    content = card["content"]
    assert content["version"] == "1.2"  # mobile Teams
    assert content["actions"][0]["title"] == "Review & acknowledge"
    assert content["actions"][0]["url"] == "https://app/alerts"
    assert "0 open ticket" in summary


def test_build_lists_tickets_capacity_and_unacked_banner():
    tickets = [
        {"checkType": "throttle", "resource": "capacity", "severity": "warn",
         "materialityReason": "throttle 8m", "currentlyActive": True},
        {"checkType": "cross_user", "resource": "Fin/Sales", "severity": "info",
         "currentlyActive": False},
    ]
    md, card, summary = build_daily_summary(
        open_tickets=tickets, capacity={"peakCuPct": 132.0, "throttleMinutes": 8.0},
        coverage_gaps=["true CU% reached 82% with zero monitored activity"],
        date_str="2026-08-05", app_url="https://app", unacked_prior=2)
    blob = json.dumps(card)
    assert "2 earlier daily summaries still awaiting acknowledgement" in md
    assert "awaiting acknowledgement" in blob
    assert "132%" in md and "Peak true CU% today" in blob
    assert "(currently inactive)" in blob          # the inactive ticket is flagged, still listed
    assert "1 warning, 1 info" in blob
    assert "2 open ticket" in summary and "2 prior unacknowledged" in summary


# ---- orchestrator ----

def test_run_delivers_records_and_excludes_digest_rows():
    store = create_alerts_store_memory()
    store["upsert"]({"incidentKey": "throttle::capacity", "status": "active", "severity": "warn",
                     "checkType": "throttle", "resource": "capacity", "currentlyActive": True})
    # yesterday's digest, NOT acknowledged -> must re-surface and be counted
    store["upsert"]({"incidentKey": digest_key("2026-08-04"), "status": "active", "severity": "info",
                     "checkType": "daily_summary", "resource": "capacity",
                     "chatId": "digest-chat-1", "currentlyActive": True})
    posts, sink = _sink()
    writes = []

    def writer(md, title):
        writes.append((md, title))
        return "digest-chat-2"

    res = run_daily_summary(alerts_store=store, ack_store={"get": lambda c: None},
                            capacity={"peakCuPct": 80.0}, coverage_gaps=[],
                            delivery_sinks={"webhook": sink}, chat_writer=writer,
                            app_url="https://app", now_dt=NOW)

    assert res["openTickets"] == 1            # the digest row is NOT counted as an open ticket
    assert res["unackedPrior"] == 1           # yesterday's digest was never acknowledged
    assert res["delivered"] is True and res["chatId"] == "digest-chat-2"
    assert writes and writes[0][1] == "Daily summary — 2026-08-05"
    active = store["query_active"]()
    assert digest_key("2026-08-05") in active and active[digest_key("2026-08-05")]["chatId"] == "digest-chat-2"
    assert "awaiting acknowledgement" in json.dumps(posts[-1])   # banner re-surfaces


def test_run_resolves_acknowledged_prior_digest():
    store = create_alerts_store_memory()
    store["upsert"]({"incidentKey": digest_key("2026-08-04"), "status": "active", "severity": "info",
                     "checkType": "daily_summary", "resource": "capacity",
                     "chatId": "digest-chat-1", "currentlyActive": True})
    posts, sink = _sink()
    ack = {"get": lambda c: {"status": "resolved"} if c == "digest-chat-1" else None}

    res = run_daily_summary(alerts_store=store, ack_store=ack, delivery_sinks={"webhook": sink},
                            chat_writer=lambda m, t: "digest-chat-2", app_url="https://app",
                            now_dt=NOW)

    assert res["unackedPrior"] == 0
    assert digest_key("2026-08-04") not in store["query_active"]()   # acked -> resolved, dropped
    assert store["_data"][digest_key("2026-08-04")]["status"] == "resolved"
    assert "awaiting acknowledgement" not in json.dumps(posts[-1])   # no banner when nothing pending


def test_run_without_delivery_still_records_digest():
    """No webhook wired (delivery disabled) — the digest is still composed + recorded so the app
    sidebar shows it; it just isn't pushed to Teams."""
    store = create_alerts_store_memory()
    res = run_daily_summary(alerts_store=store, ack_store=None, delivery_sinks=None,
                            chat_writer=lambda m, t: "c1", app_url="https://app", now_dt=NOW)
    assert res["delivered"] is False
    assert digest_key("2026-08-05") in store["query_active"]()
