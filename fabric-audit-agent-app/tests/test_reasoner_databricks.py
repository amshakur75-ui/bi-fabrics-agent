"""Databricks-hosted Claude reasoner shim — offline (fake OpenAI-compatible client).

Proves the OpenAI-chat endpoint can drive the existing (Anthropic-shaped) reasoner unchanged.
"""
from fabric_audit_agent.adapters.clients import build_databricks_claude_client
from fabric_audit_agent.adapters.reasoner_claude import create_claude_reasoner
from fabric_audit_agent.detectors import detect_all
from fabric_audit_agent.config import DEFAULT_CONFIG


class _FakeCompletions:
    def __init__(self, text):
        self.text = text
        self.received = None

    def create(self, **kwargs):
        self.received = kwargs
        msg = type("Msg", (), {"content": self.text})()
        choice = type("Choice", (), {"message": msg})()
        return type("Resp", (), {"choices": [choice]})()


class _FakeOpenAI:
    def __init__(self, text):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(text)})()


def test_shim_translates_anthropic_shape_to_openai_chat():
    fake = _FakeOpenAI('[{"id":0,"why":"W","impact":"I","fix":["f"]}]')
    client = build_databricks_claude_client("databricks-claude-x", openai_client=fake)
    resp = client.messages.create(model="databricks-claude-x", max_tokens=512,
                                  system=[{"type": "text", "text": "SYS"}],
                                  messages=[{"role": "user", "content": "hi"}])
    assert resp.content[0].text.startswith("[")
    sent = fake.chat.completions.received
    assert sent["model"] == "databricks-claude-x" and sent["max_tokens"] == 512
    assert sent["messages"][0] == {"role": "system", "content": "SYS"}   # cache_control blocks flattened to a system msg
    assert sent["messages"][1] == {"role": "user", "content": "hi"}


def _opt_facts():
    return {"capacity": {"tenant": "Acme", "capacityId": "P", "sku": "F64", "memoryGB": 64,
                         "peakCuPct": 95, "peakAt": "t", "throttleMinutes": 20,
                         "refreshes": [{"workspace": "Fin", "dataset": "A", "scheduledAt": "06:00", "durationMin": 10, "sizeGB": 6}]}}


def test_databricks_reasoner_enriches_through_existing_logic():
    facts = _opt_facts()
    flags = detect_all(facts, DEFAULT_CONFIG)
    assert flags
    fake = _FakeOpenAI('[{"id":0,"why":"W0","impact":"I0","fix":["fa","fb"]}]')
    client = build_databricks_claude_client("ep", openai_client=fake)
    out = create_claude_reasoner(client, model="ep", config=DEFAULT_CONFIG)["reason"](facts, flags)
    assert out[0]["why"] == "W0" and out[0]["reasonedBy"] == "claude"
