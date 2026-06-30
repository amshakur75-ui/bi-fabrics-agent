"""Investigation reasoner — turns an assembled evidence bundle into an explanation + ranked
hypotheses + explicit assumptions + confidence + what-would-confirm.

Stub: deterministic, grounded ONLY in the provided evidence, abstains when confidence is
insufficient. The LLM lives at the agent-loop level (Phase 2+); the playbook reasoner is
always this deterministic stub."""


def _stub_investigate(bundle):
    conf = (bundle.get("confidence") or {}).get("level", "low")
    cov = bundle.get("coverage") or {}
    ev = bundle.get("evidence") or []
    subject = bundle.get("subject", "the subject")

    if conf == "insufficient":
        seen = ", ".join(cov.get("workspacesSeen") or []) or "no workspaces"
        return {
            "explanation": (f"Analysis of {subject} is INSUFFICIENT: the evidence does not support a "
                            f"defensible conclusion (saw: {seen})."),
            "hypotheses": [], "assumptions": ["coverage is partial — enable monitoring on more workspaces"],
            "confidence": "insufficient",
            "whatWouldConfirm": ["enable Workspace Monitoring / Log Analytics on the relevant workspaces"],
        }

    cited = "; ".join(e.get("summary", "") for e in ev if e.get("summary"))
    return {
        "explanation": f"{subject}: {cited}." if cited else f"{subject}: see evidence.",
        "hypotheses": [e.get("summary") for e in ev if e.get("summary")][:3],
        "assumptions": ["CPU-time is a proxy for CU (monitored, not authoritative capacity CU)",
                        f"coverage limited to: {', '.join(cov.get('workspacesSeen') or []) or 'unknown'}"],
        "confidence": conf,
        "whatWouldConfirm": ["corroborate against Capacity Metrics / Capacity Events CU%"],
    }


def create_investigation_reasoner():
    return {"investigate": _stub_investigate}
