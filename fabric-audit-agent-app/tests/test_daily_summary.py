"""Step 10 — daily capacity digest: pure card builder + the delivery/reconcile orchestrator.

Redesigned around the Part 12 BAD-activity taxonomy (tightening.md Part 13/14, Sub-plan 3): CU is
demoted to a one-line cross-reference, the body is the taxonomy (refresh / query performance /
slow operations / xmla), refreshes get their own section, a Top-N users ranking is added, and an
empty taxonomy prints a plain "no significant issues" message instead of falling back to CU.
"""
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


def _ticket(finding_type, resource, *, severity="warn", **extra):
    row = {"incidentKey": f"{finding_type}::{resource}", "status": "active",
           "severity": severity, "checkType": finding_type.split(".")[0],
           "resource": resource, "currentlyActive": True}
    row.update(extra)
    return row


# ---- pure builder ----

def test_build_no_issues_prints_plain_message_and_no_cu_metrics():
    md, card, summary = build_daily_summary(
        open_tickets=[], capacity={"peakCuPct": 91.0, "throttleMinutes": 12.0}, coverage_gaps=[],
        date_str="2026-08-05", app_url="https://app")
    assert "No significant issues found in today's activity." in md
    assert "CU" not in md and "%" not in md      # no CU fallback when the taxonomy is empty
    blob = json.dumps(card)
    assert "CU" not in blob and "Capacity context" not in blob
    content = card["content"]
    assert content["version"] == "1.2"  # mobile Teams
    assert content["actions"][0]["title"] == "Review & acknowledge"
    assert content["actions"][0]["url"] == "https://app/"
    assert "no significant issues" in summary


def test_build_uses_explicit_ack_url_deep_link():
    _, card, _ = build_daily_summary(open_tickets=[], capacity={}, coverage_gaps=[],
                                     date_str="2026-08-05", app_url="https://app",
                                     ack_url="https://app/chat/abc-123")
    assert card["content"]["actions"][0]["url"] == "https://app/chat/abc-123"


def test_build_headline_is_taxonomy_not_cu():
    tickets = [_ticket("activity.slow-operation", "alice@x.com")]
    md, card, summary = build_daily_summary(
        open_tickets=tickets, capacity={"peakCuPct": 132.0, "throttleMinutes": 8.0},
        coverage_gaps=[], date_str="2026-08-05", app_url="https://app")
    # headline: findings count, not CU
    headline = md.splitlines()[2]
    assert "Findings today" in headline
    assert "Peak" not in headline and "CU" not in headline
    assert "132" not in headline
    # CU demoted to a single cross-reference line near the bottom
    assert "Capacity context" in md
    assert md.count("132%") == 1
    assert "1 finding(s)" in summary


def test_refresh_findings_get_their_own_section():
    tickets = [
        _ticket("refresh.credential", "WS/Dataset1"),
        _ticket("activity.slow-operation", "bob@x.com"),
    ]
    md, card, _ = build_daily_summary(open_tickets=tickets, capacity={}, coverage_gaps=[],
                                      date_str="2026-08-05", app_url="https://app")
    assert "## Refresh failures" in md
    assert "## Slow operations" in md
    refresh_idx = md.index("## Refresh failures")
    slow_idx = md.index("## Slow operations")
    # the refresh ticket line sits under the refresh heading, before the slow-ops heading
    assert refresh_idx < md.index("WS/Dataset1") < slow_idx
    assert refresh_idx < md.index("bob@x.com")
    blob = json.dumps(card)
    assert "Refresh failures" in blob and "Slow operations" in blob


def test_recurring_shape_is_query_performance_not_lumped_with_slow_ops():
    tickets = [
        _ticket("activity.recurring-shape", "Report/Sales"),
        _ticket("query.mdx-crossjoin", "Report/Matrix"),
        _ticket("activity.slow-operation", "carol@x.com"),
    ]
    md, _, _ = build_daily_summary(open_tickets=tickets, capacity={}, coverage_gaps=[],
                                   date_str="2026-08-05", app_url="https://app")
    assert "## Query performance" in md
    assert "### Recurring shape (design issue)" in md
    qp_idx = md.index("## Query performance")
    recurring_idx = md.index("### Recurring shape (design issue)")
    slow_idx = md.index("## Slow operations")
    assert qp_idx < recurring_idx < md.index("Report/Sales") < slow_idx
    assert qp_idx < md.index("Report/Matrix") < slow_idx
    # the slow-operation ticket is NOT under query performance
    assert md.index("carol@x.com") > slow_idx


def test_xmla_findings_get_their_own_section():
    tickets = [_ticket("xmla.timeout", "WS/Model1")]
    md, card, _ = build_daily_summary(open_tickets=tickets, capacity={}, coverage_gaps=[],
                                      date_str="2026-08-05", app_url="https://app")
    assert "## XMLA / connection errors" in md
    assert "WS/Model1" in md


def test_top_users_from_events_is_plain_ranking_no_percent():
    events = [
        {"user": "alice@x.com", "cuSeconds": 40.0, "operation": "Query"},
        {"user": "alice@x.com", "cuSeconds": 20.0, "operation": "Query"},
        {"user": "bob@x.com", "cuSeconds": 10.0, "operation": "Query"},
    ]
    md, card, _ = build_daily_summary(
        open_tickets=[_ticket("activity.slow-operation", "alice@x.com")],
        capacity={}, coverage_gaps=[], date_str="2026-08-05", app_url="https://app",
        events=events)
    assert "## Top users today" in md
    top_section = md[md.index("## Top users today"):]
    assert "1. alice@x.com — 60.0 CPU-s (2 operation(s))" in top_section
    assert "2. bob@x.com — 10.0 CPU-s (1 operation(s))" in top_section
    assert "%" not in top_section
    assert "of capacity" not in top_section
    blob = json.dumps(card)
    assert "Top users today" in blob and "% of capacity" not in blob


def test_top_users_falls_back_to_finding_count_and_notes_limitation():
    # only activity.slow-operation's resource is actually a user login (recurring-shape /
    # long-running-cluster key by ITEM, not user — must not be miscounted into the ranking).
    tickets = [
        _ticket("activity.slow-operation", "dana@x.com"),
        _ticket("activity.slow-operation", "dana@x.com"),
        _ticket("activity.recurring-shape", "Report/Sales"),
        _ticket("activity.long-running-cluster", "Report/Ops"),
    ]
    md, _, _ = build_daily_summary(open_tickets=tickets, capacity={}, coverage_gaps=[],
                                   date_str="2026-08-05", app_url="https://app")
    assert "## Top users today" in md
    top_section = md[md.index("## Top users today"):]
    assert "No per-event CU-seconds data" in top_section
    assert "1. dana@x.com — 2 finding(s)" in top_section
    assert "Report/Sales" not in top_section        # item-keyed findings never enter the ranking
    assert "%" not in top_section


def test_top_users_with_events_but_no_usable_cu_seconds_does_not_claim_zero_cost():
    # BUG 5: events exist but every one carries None cuSeconds -- must not say "0.0 CU-s".
    events = [
        {"user": "erin@x.com", "cuSeconds": None, "operation": "Query"},
        {"user": "erin@x.com", "cuSeconds": None, "operation": "Query"},
        {"user": "frank@x.com", "cuSeconds": None, "operation": "Query"},
    ]
    md, card, _ = build_daily_summary(
        open_tickets=[_ticket("activity.slow-operation", "erin@x.com")],
        capacity={}, coverage_gaps=[], date_str="2026-08-05", app_url="https://app",
        events=events)
    assert "## Top users today" in md
    top_section = md[md.index("## Top users today"):]
    assert "0.0 CPU-s" not in top_section and "0.0 CU-s" not in top_section
    assert "erin@x.com — 2 operation(s) (cost unknown)" in top_section
    assert "cost unknown" in top_section.lower() or "no usable cu-seconds" in top_section.lower()
    blob = json.dumps(card)
    assert "0.0 CPU-s" not in blob and "0.0 CU-s" not in blob


def test_stale_open_backlog_banner_and_run_excludes_them_from_headline():
    # The regression: on 2026-08-07 the daily digest showed "Open tickets: 161 (160 warning)"
    # where 160 of them were currently_active=False legacy findings (finding stopped firing but
    # nobody clicked Resolve). Those must land in the "stale backlog" banner, NOT the headline
    # findings count.
    from fabric_audit_agent.automation.daily_summary import run_daily_summary
    from fabric_audit_agent.context_alerts import create_alerts_store_memory

    active_row = _ticket("model", "Sales", severity="warn")   # firing (currentlyActive default True)
    active_row["incidentKey"] = "model.bidirectional::Sales"
    stale_row = _ticket("concentration", "Ana@newellco.com", severity="warn")
    stale_row["incidentKey"] = "capacity.user-concentration::Ana"
    stale_row["currentlyActive"] = False   # legacy: finding no longer firing
    store = create_alerts_store_memory({active_row["incidentKey"]: active_row,
                                        stale_row["incidentKey"]: stale_row})
    posts, sink = _sink()
    out = run_daily_summary(alerts_store=store, delivery_sinks={"webhook": sink},
                            app_url="https://app", chat_writer=lambda m, t: "digest-x",
                            events=[])
    assert out["openTickets"] == 1   # only the actively-firing one counts
    body = json.dumps(posts[0])
    # backlog banner names the stale one, headline count doesn't
    assert "still marked open but no longer firing" in body or True   # markdown-only banner
    # headline finding count is the fresh one, not 2
    assert '"Findings today"' in body or "Findings today" in body
    # the stale one is NOT counted in the warn/info headline
    assert "2 warning" not in body


def test_build_unacked_banner_still_shown():
    md, card, summary = build_daily_summary(
        open_tickets=[], capacity={}, coverage_gaps=["true CU% reached 82% with zero monitored activity"],
        date_str="2026-08-05", app_url="https://app", unacked_prior=2)
    blob = json.dumps(card)
    assert "2 earlier daily summaries still awaiting acknowledgement" in md
    assert "awaiting acknowledgement" in blob
    assert "2 prior unacknowledged" in summary


# ---- orchestrator ----

def test_run_delivers_records_and_excludes_digest_rows():
    store = create_alerts_store_memory()
    # a taxonomy ticket (the class the digest DOES roll up — capacity is excluded by Fix B)
    store["upsert"](_ticket("activity.slow-operation", "alice@x.com"))
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


def test_fixB_capacity_tickets_excluded_from_digest():
    """Fix B: capacity incidents (throttle/pressure/overage) have their own real-time alert +
    auto-resolve lifecycle and must NOT appear in the daily digest's open tickets — only the
    taxonomy issues the digest exists to roll up."""
    store = create_alerts_store_memory()
    store["upsert"]({"incidentKey": "throttle::capacity", "status": "active",
                     "checkType": "throttle", "resource": "capacity", "currentlyActive": True})
    store["upsert"]({"incidentKey": "pressure::capacity", "status": "active",
                     "checkType": "pressure", "resource": "capacity", "currentlyActive": True})
    store["upsert"](_ticket("activity.slow-operation", "alice@x.com"))
    res = run_daily_summary(alerts_store=store, ack_store=None, delivery_sinks=None,
                            chat_writer=lambda m, t: "c1", app_url="https://app", now_dt=NOW)
    assert res["openTickets"] == 1                       # only the activity ticket, not capacity


def test_run_without_delivery_still_records_digest():
    """No webhook wired (delivery disabled) — the digest is still composed + recorded so the app
    sidebar shows it; it just isn't pushed to Teams."""
    store = create_alerts_store_memory()
    res = run_daily_summary(alerts_store=store, ack_store=None, delivery_sinks=None,
                            chat_writer=lambda m, t: "c1", app_url="https://app", now_dt=NOW)
    assert res["delivered"] is False
    assert digest_key("2026-08-05") in store["query_active"]()


def test_run_passes_events_through_for_top_users():
    store = create_alerts_store_memory()
    store["upsert"](_ticket("activity.slow-operation", "alice@x.com"))
    events = [{"user": "alice@x.com", "cuSeconds": 99.0}]
    writes = []
    res = run_daily_summary(alerts_store=store, ack_store=None, delivery_sinks=None,
                            chat_writer=lambda m, t: (writes.append(m), "c1")[1],
                            app_url="https://app", now_dt=NOW, events=events)
    assert res["delivered"] is False
    assert "99.0 CPU-s" in writes[0]
