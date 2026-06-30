"""Coded investigation playbooks (the high-stakes, reliable paths). Deterministic orchestration:
collect -> locate -> baseline/correlate -> assemble evidence -> reasoner explains/abstains.
Read-only; pure given injected collector + reasoner."""
from .evidence import build_coverage, assess_confidence, evidence_item


def investigate_user(collector, reasoner, user, days=30, config=None):
    facts = collector["collect"]() or {}
    coverage = build_coverage(facts)
    users = facts.get("users") or []
    match = next((u for u in users if (u.get("user") or "").lower() == (user or "").lower()), None)

    if match is None:
        confidence = assess_confidence(facts, found=False, corroborating_sources=0)
        bundle = {"subject": f"user {user}", "coverage": coverage, "confidence": confidence,
                  "evidence": [], "findings": []}
        return {"subject": f"user {user}", "abstained": True, "coverage": coverage,
                "confidence": confidence, "evidence": [], "result": reasoner["investigate"](bundle)}

    cap = facts.get("capacity") or {}
    corroborating = 1 + (1 if cap.get("peakCuPct") is not None else 0)
    confidence = assess_confidence(facts, found=True, corroborating_sources=corroborating)

    ev = [evidence_item("attribution",
                        f"{match['user']} = {round(match.get('sharePct', 0), 1)}% of monitored CU "
                        f"via {len(match.get('topItems') or [])} item(s)", match)]
    if cap.get("peakCuPct") is not None:
        ev.append(evidence_item("capacity",
                                f"capacity peaked {cap['peakCuPct']}% ({cap.get('throttleMinutes', 0)} min throttled)",
                                cap))

    bundle = {"subject": f"user {match['user']}", "coverage": coverage, "confidence": confidence,
              "evidence": ev, "findings": []}
    return {"subject": f"user {match['user']}", "abstained": False, "coverage": coverage,
            "confidence": confidence, "evidence": ev, "result": reasoner["investigate"](bundle)}
