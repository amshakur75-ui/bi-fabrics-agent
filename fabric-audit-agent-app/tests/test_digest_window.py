"""The digest fires twice a day and the two cards must not be confusable.

The evening card is the whole day. The NOON card is meant to be "this morning" — a trailing ago(1d)
there would drag in yesterday afternoon and evening and present them as today's activity, which is
the same class of wrongness as ranking 15 minutes and calling it "today" (round 9).
"""
from datetime import datetime, timezone

from fabric_audit_agent.automation.daily_summary import build_daily_summary
from fabric_audit_agent.job import _digest_window


def _utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_the_noon_run_looks_back_to_5am_local():
    win, label, start = _digest_window(now=_utc(2026, 8, 11, 16), env={})   # 12:00 EDT
    assert win == "420m", "noon minus 05:00 is seven hours"
    assert label == "since 05:00 EDT"
    # The START is what scopes which findings get listed, so it must be the real 05:00 local
    # instant (09:00 UTC in EDT), not merely consistent with the lookback string.
    assert start == _utc(2026, 8, 11, 9), "05:00 EDT is 09:00 UTC"


def test_the_evening_run_still_covers_the_whole_day():
    win, label, start = _digest_window(now=_utc(2026, 8, 11, 22), env={})   # 18:00 EDT
    assert (win, label) == ("1d", "last 24h")
    assert start == _utc(2026, 8, 10, 22), "the evening window is a trailing 24h"


def test_five_am_tracks_DST_rather_than_a_fixed_offset():
    """A fixed UTC offset would drift an hour twice a year and silently move what "5am" means."""
    summer = _digest_window(now=_utc(2026, 8, 11, 16), env={})[:2]   # 12:00 EDT
    winter = _digest_window(now=_utc(2026, 1, 15, 17), env={})[:2]   # 12:00 EST
    assert summer[0] == winter[0] == "420m"
    assert summer[1].endswith("EDT") and winter[1].endswith("EST")


def test_a_run_before_5am_falls_back_to_the_full_day():
    """The wider window is the safe direction: it can include extra activity, never hide any."""
    win, label, _start = _digest_window(now=_utc(2026, 8, 11, 8), env={})
    assert (win, label) == ("1d", "last 24h")


def test_an_unresolvable_timezone_falls_back_to_the_full_day():
    win, label, _start = _digest_window(now=_utc(2026, 8, 11, 16),
                                        env={"FABRIC_DISPLAY_TZ": "Not/AZone"})
    assert (win, label) == ("1d", "last 24h")


def test_an_off_schedule_morning_run_uses_the_real_elapsed_time():
    """Manual/retry runs must measure from 05:00, not assume the scheduled hour."""
    assert _digest_window(now=_utc(2026, 8, 11, 11, 35), env={})[0] == "155m"   # 07:35 EDT


def test_the_card_says_which_window_it_covers():
    """Without this the noon and 6pm cards are indistinguishable, and a reader comparing them would
    read the morning's smaller numbers as an improvement rather than a shorter window."""
    md, card, _ = build_daily_summary(
        open_tickets=[], capacity={"peakCuPct": 71.0, "throttleMinutes": 0.0}, coverage_gaps=[],
        date_str="2026-08-11", window_label="since 05:00 EDT")
    assert "since 05:00 EDT" in md
    import json
    assert "since 05:00 EDT" in json.dumps(card)


def test_the_label_is_optional_so_existing_callers_are_unchanged():
    md, _card, _ = build_daily_summary(
        open_tickets=[], capacity={}, coverage_gaps=[], date_str="2026-08-11")
    assert "2026-08-11" in md and "·" not in md.splitlines()[0]


def test_the_default_now_works_because_that_is_the_only_path_production_takes():
    """This test exists because its absence shipped a NameError to production.

    job.py imports datetime INSIDE each function rather than at module scope, and _digest_window
    initially relied on the module-level name. Every other test here passes now= explicitly, so the
    suite was green against a helper that raised on the one path the job actually uses (it calls
    with now=None). The deployed digest failed on its first run.
    """
    win, label, start = _digest_window(env={})
    assert win and label and start, "must resolve a window without an injected clock"
    assert win == "1d" or win.endswith("m")


# ---- only this window's findings, plus a synthesis --------------------------

def _ticket(key, first_alerted):
    return {"incidentKey": key, "checkType": "activity", "status": "active",
            "currentlyActive": True, "severity": "warn", "resource": "aaron@newellco.com",
            "firstAlertedAt": first_alerted, "chatId": None}


def _store(rows):
    return {"query_active": lambda: dict(rows), "query_pending": lambda: {},
            "query_informational": lambda: {}, "upsert": lambda r: None,
            "resolve": lambda k, t: None}


def test_findings_from_before_the_window_are_counted_not_relisted():
    """`active` is every open row in the shared table, so the digest relisted findings first seen
    DAYS ago under a heading that says "Findings today" — the same tickets on every card, drowning
    whatever actually happened in this window."""
    from fabric_audit_agent.automation.daily_summary import run_daily_summary

    rows = {"old": _ticket("activity.slow-operation::old", "2026-08-06T09:00:00Z"),
            "new": _ticket("activity.slow-operation::new", "2026-08-11T10:00:00Z")}
    out = run_daily_summary(alerts_store=_store(rows), window_start=_utc(2026, 8, 11, 9),
                            window_label="since 05:00 EDT", now_dt=_utc(2026, 8, 11, 16))
    assert out["openTickets"] == 1, "only the in-window finding is listed"


def test_a_finding_with_no_timestamp_is_listed_rather_than_hidden():
    """Safe direction: a listed finding can be checked by a human, a hidden one cannot."""
    from fabric_audit_agent.automation.daily_summary import run_daily_summary

    rows = {"x": _ticket("activity.slow-operation::x", None)}
    out = run_daily_summary(alerts_store=_store(rows), window_start=_utc(2026, 8, 11, 9),
                            now_dt=_utc(2026, 8, 11, 16))
    assert out["openTickets"] == 1


def test_the_chat_body_follows_the_list_with_a_synthesis():
    """Opening the chat from "Review & acknowledge" gave a relist and stopped, leaving the reader to
    do the aggregation themselves. The list is not an answer."""
    md, card, _ = build_daily_summary(
        open_tickets=[{"checkType": "activity", "severity": "warn", "detail": "x",
                       "resource": "aaron@newellco.com", "incidentKey": "activity.slow::a"}],
        capacity={"peakCuPct": 187.0, "throttleMinutes": 9.0}, coverage_gaps=[],
        date_str="2026-08-11", window_label="since 05:00 EDT", carried_over=38)
    headings = [l for l in md.splitlines() if l.startswith("## ")]
    assert headings, "the card should have itemised sections"
    assert headings[-1] == "## Summary", "the synthesis must come AFTER the list, not replace it"
    assert "187%" in md.split("## Summary")[1], "the summary states what the window meant"
    assert "38 older finding" in md, "the carried-over backlog is counted, not silently dropped"


def test_the_synthesis_never_claims_health_it_cannot_see():
    md, _card, _ = build_daily_summary(
        open_tickets=[], capacity={}, coverage_gaps=[], date_str="2026-08-11",
        window_label="since 05:00 EDT")
    tail = md.split("## Summary")[1]
    assert "UNKNOWN" in tail, "no CU reading must read as unknown, not as healthy"
