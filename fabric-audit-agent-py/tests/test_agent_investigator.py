from fabric_audit_agent.agent.investigator import investigate
from fabric_audit_agent.agent.scripted_client import Block as _B, Message as _M, ScriptedClient as FakeClient


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
