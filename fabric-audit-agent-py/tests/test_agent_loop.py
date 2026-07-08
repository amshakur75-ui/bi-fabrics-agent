# tests/test_agent_loop.py
import json
from fabric_audit_agent.agent.loop import run_tool_loop
from fabric_audit_agent.agent.scripted_client import Block as _B, Message as _M, ScriptedClient as FakeClient


def _dispatch():
    calls = []
    def h(inp):
        calls.append(inp)
        return {"abstained": False, "evidence": [{"summary": "x@co = 90% monitored CU"}]}
    return {"investigate_user": h}, calls


def test_loop_calls_tool_then_answers():
    scripted = [
        _M([_B("tool_use", id="t1", name="investigate_user", input={"user": "x@co"})], "tool_use"),
        _M([_B("text", text="x@co drives 90% of monitored CU.")], "end_turn"),
    ]
    dispatch, calls = _dispatch()
    out = run_tool_loop(FakeClient(scripted), model="m", system="s",
                        messages=[{"role": "user", "content": "who is driving it?"}],
                        tools=[{"name": "investigate_user"}], dispatch=dispatch, max_steps=6)
    assert calls == [{"user": "x@co"}]                          # the tool ran with the model's input
    assert "90%" in out["text"] and out["stoppedReason"] == "answer"
    assert out["trajectory"] == [{"tool": "investigate_user", "input": {"user": "x@co"}}]
    # additive: toolResults carries the actual handler return for executed (non-cached) calls
    assert out["toolResults"][0]["tool"] == "investigate_user"
    assert "evidence" in out["toolResults"][0]["result"]


def test_loop_dedups_identical_tool_calls():
    tu = _B("tool_use", id="t1", name="investigate_user", input={"user": "x@co"})
    scripted = [
        _M([tu], "tool_use"),
        _M([_B("tool_use", id="t2", name="investigate_user", input={"user": "x@co"})], "tool_use"),
        _M([_B("text", text="done")], "end_turn"),
    ]
    dispatch, calls = _dispatch()
    out = run_tool_loop(FakeClient(scripted), model="m", system="s",
                        messages=[{"role": "user", "content": "?"}],
                        tools=[{"name": "investigate_user"}], dispatch=dispatch, max_steps=6)
    assert len(calls) == 1                                       # identical call ran only once (read-only dedup)
    assert out["text"] == "done"
    # additive: only the first (executed) call appears in toolResults; the cached re-call is excluded
    assert len(out["toolResults"]) == 1
    assert out["toolResults"][0]["tool"] == "investigate_user"


def test_forced_final_step_tells_the_model_the_budget_is_gone():
    """Observed live: withholding tools on the last step without saying why made the model
    NARRATE its next intended tool call instead of answering. The loop must inject an explicit
    budget-exhausted instruction before the forced-answer create()."""
    seen = []

    class _Recorder:
        class messages:
            @staticmethod
            def create(model=None, max_tokens=None, system=None, messages=None, tools=None):
                seen.append({"messages": list(messages), "tools": list(tools or [])})
                if len(seen) < 3:   # keep calling tools until the forced final step
                    return _M([_B("tool_use", id=f"t{len(seen)}", name="investigate_user",
                                  input={"user": f"u{len(seen)}@co"})], "tool_use")
                return _M([_B("text", text="final answer")], "end_turn")

    dispatch, _ = _dispatch()
    out = run_tool_loop(_Recorder(), model="m", system="s",
                        messages=[{"role": "user", "content": "?"}],
                        tools=[{"name": "investigate_user"}], dispatch=dispatch, max_steps=3)
    assert out["text"] == "final answer" and out["stoppedReason"] == "answer"
    final_call = seen[-1]
    assert final_call["tools"] == []                                   # tools withheld
    nudge = final_call["messages"][-1]
    assert nudge["role"] == "user" and "budget exhausted" in nudge["content"].lower()
    # earlier steps must NOT carry the nudge
    assert not any("budget exhausted" in str(m).lower() for m in seen[0]["messages"])


def test_no_nudge_when_model_answers_directly():
    """A direct answer (no tool calls ever) must not get the budget message -- there's no
    investigation to conclude."""
    seen = []

    class _Recorder:
        class messages:
            @staticmethod
            def create(model=None, max_tokens=None, system=None, messages=None, tools=None):
                seen.append(list(messages))
                return _M([_B("text", text="direct")], "end_turn")

    out = run_tool_loop(_Recorder(), model="m", system="s",
                        messages=[{"role": "user", "content": "?"}],
                        tools=[{"name": "investigate_user"}], dispatch={}, max_steps=1)
    assert out["text"] == "direct"
    assert not any("budget exhausted" in str(m).lower() for m in seen[0])


def test_loop_spotlights_tool_results():
    """Tool result content fed back in the follow-up create() must carry the UNTRUSTED marker
    AND still contain the underlying data (spotlighting, not stripping)."""
    scripted = [
        _M([_B("tool_use", id="t1", name="investigate_user", input={"user": "x@co"})], "tool_use"),
        _M([_B("text", text="answer")], "end_turn"),
    ]
    dispatch, _ = _dispatch()
    client = FakeClient(scripted)
    run_tool_loop(client, model="m", system="s",
                  messages=[{"role": "user", "content": "?"}],
                  tools=[{"name": "investigate_user"}], dispatch=dispatch, max_steps=6)
    # The second create() call receives the tool_result message; inspect its messages
    second_call_messages = client.calls[1]["messages"]
    # the tool_result turn is the user message whose content is a LIST of blocks
    # (not the original plain-string user prompt)
    tool_result_msg = next(m for m in second_call_messages
                           if m["role"] == "user" and isinstance(m["content"], list))
    content_str = tool_result_msg["content"][0]["content"]
    assert "UNTRUSTED" in content_str                            # spotlighting delimiter present
    assert "90%" in content_str                                  # underlying data still present


def test_loop_forces_answer_on_last_step():
    # model keeps asking for tools; on the final step it is called with NO tools -> must answer
    scripted = [
        _M([_B("tool_use", id="t1", name="investigate_user", input={"user": "a"})], "tool_use"),
        _M([_B("text", text="final under budget")], "end_turn"),
    ]
    dispatch, _ = _dispatch()
    client = FakeClient(scripted)
    out = run_tool_loop(client, model="m", system="s",
                        messages=[{"role": "user", "content": "?"}],
                        tools=[{"name": "investigate_user"}], dispatch=dispatch, max_steps=2)
    assert client.calls[-1]["tools"] == []                      # tools stripped on the last allowed step
    assert out["stoppedReason"] == "answer"


def test_loop_budget_exhaustion_message():
    """Pathological case: even the final stripped-tools call returns tool_use (a misbehaving model).
    The loop should exhaust the step budget and return stoppedReason='budget' with a non-empty message."""
    # Step 0: tool_use; step 1 (final, tools=[]): also tool_use -> loop falls through -> budget
    scripted = [
        _M([_B("tool_use", id="t1", name="investigate_user", input={"user": "a"})], "tool_use"),
        _M([_B("tool_use", id="t2", name="investigate_user", input={"user": "a"})], "tool_use"),
    ]
    dispatch, _ = _dispatch()
    out = run_tool_loop(FakeClient(scripted), model="m", system="s",
                        messages=[{"role": "user", "content": "?"}],
                        tools=[{"name": "investigate_user"}], dispatch=dispatch, max_steps=2)
    assert out["stoppedReason"] == "budget"
    assert isinstance(out["text"], str) and len(out["text"]) > 0   # honest non-empty budget message
