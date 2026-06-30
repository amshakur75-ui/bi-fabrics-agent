"""Coded investigation playbooks (the high-stakes, reliable paths). Deterministic orchestration:
collect -> locate -> baseline/correlate -> assemble evidence -> reasoner explains/abstains.
Read-only; pure given injected collector + reasoner."""
from .evidence import build_coverage, assess_confidence, evidence_item
from .baseline import compute_baseline, compare_to_baseline


def _finish(subject, coverage, confidence, evidence, abstained, reasoner):
    """Assemble the investigation bundle, call the reasoner, and return the envelope."""
    bundle = {"subject": subject, "coverage": coverage, "confidence": confidence,
              "evidence": evidence, "findings": []}
    return {"subject": subject, "abstained": abstained, "coverage": coverage,
            "confidence": confidence, "evidence": evidence,
            "result": reasoner["investigate"](bundle)}


def investigate_user(collector, reasoner, user, days=30):
    facts = collector["collect"]() or {}
    coverage = build_coverage(facts)
    users = facts.get("users") or []
    match = next((u for u in users if (u.get("user") or "").lower() == (user or "").lower()), None)

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


def investigate_capacity_spike(collector, reasoner, when=None):
    # `when` is reserved for time-window filtering once capacity events carry per-window timestamps (Phase 3/4).
    facts = collector["collect"]() or {}
    coverage = build_coverage(facts)
    cap = facts.get("capacity") or {}

    if cap.get("peakCuPct") is None:
        confidence = assess_confidence(found=False, corroborating_sources=0)
        return _finish("capacity spike", coverage, confidence, [], True, reasoner)

    items = sorted(facts.get("items") or [], key=lambda it: -(it.get("sharePct") or 0))
    top = items[0] if items else None
    corroborating = 1 + (1 if items else 0)
    confidence = assess_confidence(found=True, corroborating_sources=corroborating)

    ev = [evidence_item("capacity",
                        f"capacity peaked {cap['peakCuPct']}% ({cap.get('throttleMinutes', 0)} min throttled)",
                        cap)]
    if top:
        label = "monitored CU" if top.get("attributionMode") == "cost" else "capacity CU"
        tu = (top.get("topUsers") or [{}])[0].get("user")
        ev.append(evidence_item("concentration",
                                f"\"{top.get('name')}\" = {round(top.get('sharePct', 0), 1)}% of {label}"
                                + (f" (top user {tu})" if tu else ""), top))

    return _finish("capacity spike", coverage, confidence, ev, False, reasoner)
