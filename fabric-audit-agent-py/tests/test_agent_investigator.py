from fabric_audit_agent.agent.investigator import investigate
try:
    from tests.test_agent_loop import FakeClient, _M, _B
except ImportError:
    # Inline fallback if tests/ is not importable as a package
    class _B:
        def __init__(self, type, text=None, id=None, name=None, input=None):
            self.type, self.text, self.id, self.name, self.input = type, text, id, name, input

    class _M:
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


def test_investigate_end_to_end_with_fake_client(monkeypatch):
    for v in ("FABRIC_CSV_PATHS", "FABRIC_CLIENT_ID", "FABRIC_KUSTO_CLUSTER",
              "FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_LA_WORKSPACE_ID"):
        monkeypatch.delenv(v, raising=False)
    scripted = [
        _M([_B("tool_use", id="t1", name="investigate_capacity_spike", input={})], "tool_use"),
        _M([_B("text", text="No live capacity signal — I can't see a spike; enable monitoring.")], "end_turn"),
    ]
    out = investigate([{"role": "user", "content": "why did capacity spike?"}], FakeClient(scripted))
    assert out["trajectory"][0]["tool"] == "investigate_capacity_spike"   # it used a real tool
    assert "enable monitoring" in out["output_text"].lower()
    assert out["stoppedReason"] == "answer"
