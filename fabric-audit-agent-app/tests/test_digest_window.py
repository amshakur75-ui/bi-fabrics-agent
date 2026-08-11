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
    win, label = _digest_window(now=_utc(2026, 8, 11, 16), env={})   # 12:00 EDT
    assert win == "420m", "noon minus 05:00 is seven hours"
    assert label == "since 05:00 EDT"


def test_the_evening_run_still_covers_the_whole_day():
    win, label = _digest_window(now=_utc(2026, 8, 11, 22), env={})   # 18:00 EDT
    assert (win, label) == ("1d", "last 24h")


def test_five_am_tracks_DST_rather_than_a_fixed_offset():
    """A fixed UTC offset would drift an hour twice a year and silently move what "5am" means."""
    summer = _digest_window(now=_utc(2026, 8, 11, 16), env={})       # 12:00 EDT
    winter = _digest_window(now=_utc(2026, 1, 15, 17), env={})       # 12:00 EST
    assert summer[0] == winter[0] == "420m"
    assert summer[1].endswith("EDT") and winter[1].endswith("EST")


def test_a_run_before_5am_falls_back_to_the_full_day():
    """The wider window is the safe direction: it can include extra activity, never hide any."""
    assert _digest_window(now=_utc(2026, 8, 11, 8), env={}) == ("1d", "last 24h")


def test_an_unresolvable_timezone_falls_back_to_the_full_day():
    assert _digest_window(now=_utc(2026, 8, 11, 16),
                          env={"FABRIC_DISPLAY_TZ": "Not/AZone"}) == ("1d", "last 24h")


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
    win, label = _digest_window(env={})
    assert win and label, "must resolve a window without an injected clock"
    assert win == "1d" or win.endswith("m")
