"""Score investigation playbooks against golden cases: groundedness (every hypothesis traces to
evidence) + coverage-honesty (abstain iff expected). Offline, uses the stub reasoner."""
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


# --- agent-trajectory scoring (appended) ---
from ..agent.investigator import investigate
from ..agent.scripted_client import Block, Message, ScriptedClient


def _client_from_script(script):
    """Map a JSON script list (dicts with type/text/name/input) to a ScriptedClient."""
    msgs = []
    for b in script:
        block = Block(b["type"], text=b.get("text"), id=b.get("id", "t"),
                      name=b.get("name"), input=b.get("input"))
        stop = "tool_use" if b["type"] == "tool_use" else "end_turn"
        msgs.append(Message([block], stop))
    return ScriptedClient(msgs)


_AGENT_CASES = os.path.join(os.path.dirname(__file__), "agent_cases.json")


def score_agent_case(case, client_factory=None):
    if client_factory is None:
        client_factory = lambda c: _client_from_script(c["script"])
    out = investigate(case["messages"], client_factory(case))
    tools_used = [t["tool"] for t in out["trajectory"]]
    text = out["output_text"].lower()
    abstained = any(w in text for w in ("can't", "cannot", "insufficient", "enable monitoring", "abstain"))
    abstain_ok = abstained == bool(case.get("expectAbstain"))

    if case.get("expectAbstain"):
        # abstaining cases: groundedness only requires the expected tool ran
        grounded_ok = case.get("expectTool") in tools_used if case.get("expectTool") else True
    else:
        # non-abstaining cases: expected tool ran AND at least one numeric OR capitalized/email-like
        # entity token from output_text appears in the tool-results JSON (stronger than any >3-char word).
        tool_ran = case.get("expectTool") in tools_used if case.get("expectTool") else True
        tool_results_json = " ".join(
            json.dumps(tr["result"], ensure_ascii=False) for tr in out.get("toolResults", [])
        )
        tool_results_lower = tool_results_json.lower()
        # Numeric tokens: runs of digits (e.g. "96", "42") extracted from raw output text
        import re as _re
        numeric_tokens = _re.findall(r'\d+', out["output_text"])
        # Entity tokens: words with an uppercase letter in the original text (e.g. "Finance") or
        # email-like tokens (contain @); skip short words.
        raw_words = out["output_text"].split()
        entity_tokens = [w.strip(".,;:\"'()") for w in raw_words
                         if (any(c.isupper() for c in w) or "@" in w) and len(w.strip(".,;:\"'()")) > 3]
        # At least one numeric token OR entity token must appear in the tool-results JSON
        traced = (
            any(n in tool_results_json for n in numeric_tokens) or
            any(e.lower() in tool_results_lower for e in entity_tokens)
        )
        grounded_ok = tool_ran and traced

    return {"name": case["name"], "groundedOk": grounded_ok, "abstainOk": abstain_ok,
            "passed": grounded_ok and abstain_ok}


def run_agent_suite(path=None):
    with open(path or _AGENT_CASES, "r", encoding="utf-8") as fh:
        cases = json.load(fh)
    results = [score_agent_case(c) for c in cases]
    return {"total": len(results), "passed": sum(1 for r in results if r["passed"]), "cases": results}
