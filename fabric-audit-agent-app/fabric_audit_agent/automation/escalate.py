"""Escalate a persistently-unresolved Warning to Critical. Port of ``core/automation/escalate.js``.

The original rule was purely presence-based: Warning -> Critical when the same key appeared in the
two most recent prior runs ("unresolved 3 consecutive runs"). That is a RUN count, and it silently
inherited whatever the schedule happened to be. The Node original ran daily, so three runs meant
three days -- a fair definition of "nobody has dealt with this". This deployment runs the sweep
HOURLY, so the same code meant three HOURS, and the estate reported 37 of 41 findings as Critical.

Nothing was wrong with the detectors; the word "Critical" had simply stopped carrying information.
Every surface downstream reads it -- the digest's severity counts, the notification center's
ordering, the ticket glyphs -- so an inflated Critical count makes the whole product harder to read
and trains the reader to ignore the label.

The rule is now elapsed TIME, with the presence requirement kept as an anti-flapping guard:

  * the key must be present in the two most recent prior runs (unchanged -- a finding that comes and
    goes has not been "unresolved", it has been intermittent), AND
  * it must have been CONTINUOUSLY present for at least ``escalate_after_hours``.

That makes the threshold mean the same thing regardless of how often the sweep runs. At the default
24 hours on an hourly cron a finding must survive a full day -- appearing in at least two daily
digests -- before it is called Critical. Change the cadence to every 15 minutes and the definition
does not drift.

Pure. Override the window with ``FABRIC_ESCALATE_AFTER_HOURS`` (0 disables the time floor and
restores the old presence-only behaviour).
"""
import os

DEFAULT_ESCALATE_AFTER_HOURS = 24.0


def _hours_setting(env=None):
    env = env if env is not None else os.environ
    raw = env.get("FABRIC_ESCALATE_AFTER_HOURS")
    if raw is not None and str(raw).strip():
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return DEFAULT_ESCALATE_AFTER_HOURS


def _run_ms(run):
    """Epoch ms for a history run, or None. Accepts the ISO ``runAt`` the stores write."""
    ts = (run or {}).get("runAt") or (run or {}).get("run_at")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return float(ts)
    if not ts:
        return None
    from ..timefmt import parse_iso_utc
    dt = parse_iso_utc(ts)
    return dt.timestamp() * 1000.0 if dt is not None else None


def _continuous_since_ms(key, history):
    """Epoch ms of the OLDEST run in the unbroken streak of runs containing ``key``, or None.

    Walks backwards from the newest run and stops at the first run without the key, so an
    intermittent finding is measured from when it most recently came back -- not from the first time
    it was ever seen. "Unresolved for a day" has to mean a day of continuous presence.
    """
    oldest = None
    for run in reversed(history):
        if not any((rf or {}).get("key") == key for rf in (run.get("findings") or [])):
            break
        ms = _run_ms(run)
        if ms is not None:
            oldest = ms
    return oldest


def apply_escalation(findings, history, now_ms=None):
    history = history or []
    last_two = history[-2:]
    if len(last_two) < 2:
        return [{**f} for f in findings]

    def present_in_all(key):
        return all(any((rf or {}).get("key") == key for rf in (run.get("findings") or []))
                   for run in last_two)

    min_hours = _hours_setting()
    # "Now" defaults to the newest history run rather than the wall clock, so this stays pure and
    # testable; the caller passes the real run timestamp in production.
    if now_ms is None:
        now_ms = _run_ms(history[-1])

    def long_enough(key):
        if min_hours <= 0:
            return True                      # time floor disabled -> old presence-only behaviour
        since = _continuous_since_ms(key, history)
        if since is None or now_ms is None:
            # No usable timestamps. Fall back to the presence-only rule rather than swallowing the
            # escalation: over-grading is a readability problem, under-grading hides a real one.
            return True
        return (now_ms - since) >= min_hours * 3600_000.0

    out = []
    for f in findings:
        key = f.get("key")
        if ((f.get("score") or {}).get("level") == "Warning" and key
                and present_in_all(key) and long_enough(key)):
            hours = ""
            since = _continuous_since_ms(key, history)
            if since is not None and now_ms is not None:
                hours = f" ~{int((now_ms - since) / 3600_000.0)}h"
            out.append({**f, "score": {
                "level": "Critical",
                "reason": (f"{f['score']['reason']} (escalated: unresolved for"
                           f"{hours or ' 3 consecutive runs'})")}})
        else:
            out.append({**f})
    return out
