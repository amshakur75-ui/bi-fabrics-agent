"""Score investigation PLAYBOOKS against golden cases: groundedness (every hypothesis traces to
evidence) + coverage-honesty (abstain iff expected). Offline, uses the stub reasoner.

Scope note (ADR-001, 2026-07-29): this file now covers ONLY the playbook half of the eval
suite — testing the deterministic ``investigate_user`` / ``investigate_capacity_spike``
functions in ``fabric_audit_agent.investigation.playbooks`` with no LLM/prompt/loop involved,
which is legitimately tools-side logic. The other half (``score_agent_case`` / scripted-client
trajectory scoring against ``agent_cases.json``) moved to the chat app at
``fabric-audit-agent-app/agent_server/eval_score.py`` because it's testing agent behavior."""
import json
import os

from ..investigation.playbooks import investigate_user, investigate_capacity_spike
from ..adapters.reasoner_investigation import create_investigation_reasoner

_PLAYBOOKS = {"investigate_user": investigate_user, "investigate_capacity_spike": investigate_capacity_spike}
_CASES = os.path.join(os.path.dirname(__file__), "investigation_cases.json")


def _grounded(result):
    ev_text = " ".join(
        (e.get("summary", "") + " " + json.dumps(e.get("data", {}))) for e in result.get("evidence", [])
    ).lower()
    hyps = result.get("result", {}).get("hypotheses", []) or []
    if not hyps:
        return True   # nothing claimed -> nothing to ground
    # each hypothesis must share a meaningful token with the cited evidence
    return all(any(tok in ev_text for tok in h.lower().split() if len(tok) > 3) for h in hyps)


def score_investigation_case(case):
    pb = _PLAYBOOKS[case["playbook"]]
    collector = {"collect": lambda c=case: c["facts"]}
    args = case.get("args", {})
    if case["playbook"] == "investigate_user":
        result = pb(collector, create_investigation_reasoner(), args.get("user"), days=args.get("days", 30))
    else:
        result = pb(collector, create_investigation_reasoner(), args.get("when"))
    abstain_ok = bool(result["abstained"]) == bool(case.get("expectAbstain"))
    grounded_ok = _grounded(result)
    return {"name": case["name"], "abstainOk": abstain_ok, "groundedOk": grounded_ok,
            "passed": abstain_ok and grounded_ok}


def run_suite(path=None):
    with open(path or _CASES, "r", encoding="utf-8") as fh:
        cases = json.load(fh)
    results = [score_investigation_case(c) for c in cases]
    return {"total": len(results), "passed": sum(1 for r in results if r["passed"]), "cases": results}
