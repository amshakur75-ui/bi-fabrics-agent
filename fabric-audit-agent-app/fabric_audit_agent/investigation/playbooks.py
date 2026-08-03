"""Coded investigation playbooks (the high-stakes, reliable paths). Deterministic orchestration:
collect -> locate -> baseline/correlate -> assemble evidence -> reasoner explains/abstains.
Read-only; pure given injected collector + reasoner (+ optional pre-fetched events/series)."""
from datetime import timedelta

from .evidence import build_coverage, assess_confidence, evidence_item
from .baseline import compute_baseline, compare_to_baseline
from ..timefmt import parse_iso_utc
from ..key_utils import user_matches


def _finish(subject, coverage, confidence, evidence, abstained, reasoner):
    """Assemble the investigation bundle, call the reasoner, and return the envelope."""
    bundle = {"subject": subject, "coverage": coverage, "confidence": confidence,
              "evidence": evidence}
    return {"subject": subject, "abstained": abstained, "coverage": coverage,
            "confidence": confidence, "evidence": evidence,
            "result": reasoner["investigate"](bundle)}


def investigate_user(collector, reasoner, user, days=30):
    facts = collector["collect"]() or {}
    coverage = build_coverage(facts)
    users = facts.get("users") or []
    match = next((u for u in users if user_matches(u.get("user"), user)), None)

    if match is None:
        confidence = assess_confidence(found=False, corroborating_sources=0)
        return _finish(f"user {user}", coverage, confidence, [], True, reasoner)

    cap = facts.get("capacity") or {}
    corroborating = 1 + (1 if cap.get("peakCuPct") is not None else 0)
    confidence = assess_confidence(found=True, corroborating_sources=corroborating)

    ev = [evidence_item("attribution",
                        f"{match['user']} = {round(match.get('sharePct', 0), 1)}% of monitored CU "
                        f"via {len(match.get('topItems') or [])} item(s)", match)]
    if cap.get("peakCuPct") is not None:
        ev.append(evidence_item("capacity",
                                f"capacity peaked {cap['peakCuPct']}% ({cap.get('throttleMinutes', 0)} min throttled)",
                                cap))

    history = facts.get("history")
    if isinstance(history, dict):
        rows = history.get(match["user"])
        if rows:
            baseline = compute_baseline(rows)
            today_cu = match.get("cuSeconds") or 0
            cmp = compare_to_baseline(today_cu, baseline)
            label = ("ABOVE p95 — abnormal for this user" if cmp["shifted"]
                     else "within this user's normal range")
            summary = (f"today {today_cu} CU(s) vs p50 {baseline['p50']} over last {days}d "
                       f"(n={baseline['count']}): {label}")
            ev.append(evidence_item("baseline", summary, {"baseline": baseline, "comparison": cmp}))

    return _finish(f"user {match['user']}", coverage, confidence, ev, False, reasoner)


def _spike_window_evidence(when, events, capacity_series, window_minutes, truncated=False):
    """Evidence for the ±window around *when* from per-event telemetry: interactive-vs-refresh CU
    split, distinct users, top events, and the in-window CU% peak. Returns (evidence, found).
    ``truncated`` = the event fetch hit its row cap, so the split covers only a slice of the
    window — disclosed in the summary and data, per the honesty rules."""
    center = parse_iso_utc(when)
    if center is None:
        return (evidence_item(
            "window",
            f"could not parse when={when!r} — expected ISO UTC or 'YYYY-MM-DD HH:MM UTC'",
            {"when": when}), False)

    lo, hi = center - timedelta(minutes=window_minutes), center + timedelta(minutes=window_minutes)
    in_win = []
    for e in events or []:
        ts = parse_iso_utc(e.get("ts"))
        if ts is not None and lo <= ts <= hi:
            in_win.append(e)

    peak = None
    for p in capacity_series or []:
        ts = parse_iso_utc(p.get("ts"))
        if ts is not None and lo <= ts <= hi:
            v = p.get("cuPct")
            if v is not None and (peak is None or v > peak):
                peak = v

    if not in_win:
        return (evidence_item(
            "window",
            f"no telemetry events within ±{window_minutes} minutes of {when} in the retrieved "
            f"lookback — cannot attribute this specific peak from event data",
            {"when": when, "windowMinutes": window_minutes, "eventCount": 0,
             "windowPeakCuPct": peak}), False)

    interactive = round(sum(e.get("cuSeconds") or 0 for e in in_win if e.get("kind") == "interactive"), 1)
    refresh = round(sum(e.get("cuSeconds") or 0 for e in in_win if e.get("kind") == "refresh"), 1)
    users = len({e.get("user") for e in in_win if e.get("user")})
    top = sorted(in_win, key=lambda e: -(e.get("cuSeconds") or 0))[:3]
    driver = ("refresh-driven" if refresh > interactive
              else "interactive-driven" if interactive > refresh else "mixed")
    top_txt = "; ".join(
        f"{e.get('user')} on {e.get('item')} ({e.get('kind')}, {round(e.get('cuSeconds') or 0, 1)} CU-s)"
        for e in top)
    summary = (f"±{window_minutes}m around {when}: {len(in_win)} events from {users} users — "
               f"interactive {interactive} vs refresh {refresh} CU-s ({driver})"
               + (f"; window peak {peak}% CU" if peak is not None else "")
               + (f"; top: {top_txt}" if top_txt else "")
               + (" [event cap hit — split covers only the newest slice of the window]"
                  if truncated else ""))
    data = {"when": when, "windowMinutes": window_minutes, "eventCount": len(in_win),
            "distinctUsers": users, "interactiveCuSeconds": interactive,
            "refreshCuSeconds": refresh, "driver": driver, "windowPeakCuPct": peak,
            "topEvents": [{"ts": e.get("ts"), "user": e.get("user"), "item": e.get("item"),
                           "kind": e.get("kind"), "cuSeconds": e.get("cuSeconds")} for e in top]}
    if truncated:
        data["eventsTruncated"] = True
    return evidence_item("window", summary, data), True


def investigate_capacity_spike(collector, reasoner, when=None, events=None, capacity_series=None,
                               window_minutes=30, events_truncated=False):
    """``when`` + pre-fetched Phase-3 ``events``/``capacity_series`` scope the analysis to the
    ±``window_minutes`` around the named moment — answering "was THIS peak a refresh or
    interactive load, and whose?" instead of restating the whole-window rollup."""
    facts = collector["collect"]() or {}
    coverage = build_coverage(facts)
    cap = facts.get("capacity") or {}

    if cap.get("peakCuPct") is None:
        confidence = assess_confidence(found=False, corroborating_sources=0)
        return _finish("capacity spike", coverage, confidence, [], True, reasoner)

    items = sorted(facts.get("items") or [], key=lambda it: -(it.get("sharePct") or 0))
    top = items[0] if items else None
    corroborating = 1 + (1 if items else 0)

    ev = [evidence_item("capacity",
                        f"capacity peaked {cap['peakCuPct']}% ({cap.get('throttleMinutes', 0)} min throttled)",
                        cap)]
    if top:
        label = "monitored CU" if top.get("attributionMode") == "cost" else "capacity CU"
        tu = (top.get("topUsers") or [{}])[0].get("user")
        ev.append(evidence_item("concentration",
                                f"\"{top.get('name')}\" = {round(top.get('sharePct', 0), 1)}% of {label}"
                                + (f" (top user {tu})" if tu else ""), top))

    if when and (events is not None or capacity_series is not None):
        window_ev, found = _spike_window_evidence(when, events, capacity_series, window_minutes,
                                                  truncated=events_truncated)
        ev.append(window_ev)
        if found:
            corroborating += 1   # per-event telemetry corroborates the rollup signals

    confidence = assess_confidence(found=True, corroborating_sources=corroborating)
    return _finish("capacity spike", coverage, confidence, ev, False, reasoner)
