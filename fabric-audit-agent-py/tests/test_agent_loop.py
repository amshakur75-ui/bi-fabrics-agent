# tests/test_agent_loop.py
import json
from fabric_audit_agent.agent.loop import run_tool_loop


class _B:   # a fake Anthropic content block
    def __init__(self, type, text=None, id=None, name=None, input=None):
        self.type, self.text, self.id, self.name, self.input = type, text, id, name, input


class _M:   # a fake Anthropic message
    def __init__(self, content, stop_reason):
        self.content, self.stop_reason = content, stop_reason


class FakeClient:
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)


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
