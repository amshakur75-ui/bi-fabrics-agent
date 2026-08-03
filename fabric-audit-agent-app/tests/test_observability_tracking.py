"""Observability tracking (Task 4.10): durationMs, errored, tokenUsage per run."""
import json

import pytest

from fabric_audit_agent.pipeline import run_audit
from fabric_audit_agent.adapters import create_local_store
from fabric_audit_agent.config import DEFAULT_CONFIG


# ---------- helpers ----------

_MINIMAL_FACTS = {
    "capacity": {"capacityId": "TestCap", "sku": "F64", "memoryGB": 64,
                 "peakCuPct": 50, "peakAt": "2026-01-01T00:00:00Z",
                 "throttleMinutes": 0, "refreshes": []},
}


def _simple_collector():
    return {"collect": lambda: _MINIMAL_FACTS}


def _noop_delivery():
    return {"deliver": lambda envelope: None}


def _stub_reasoner():
    """Minimal reasoner that returns no findings (no LLM call)."""
    return {"reason": lambda facts, flags: []}


def _stub_reasoner_with_usage():
    """Reasoner that simulates token usage being captured by the Claude adapter."""
    reasoner = {"reason": lambda facts, flags: []}
    reasoner["lastUsage"] = {"inputTokens": 150, "outputTokens": 42}
    return reasoner


# ---------- success case ----------

def test_successful_run_logs_duration_and_no_error(tmp_path):
    store = create_local_store(str(tmp_path / "h.json"))
    run_audit(
        _simple_collector(), _stub_reasoner(), _noop_delivery(),
        store=store, config=DEFAULT_CONFIG,
    )
    history = store["history"]()
    assert len(history) == 1
    rec = history[0]
    assert isinstance(rec["durationMs"], int) and rec["durationMs"] >= 0
    assert rec["errored"] is False


# ---------- token usage present ----------

def test_token_usage_included_when_reasoner_provides_it(tmp_path):
    store = create_local_store(str(tmp_path / "h.json"))
    run_audit(
        _simple_collector(), _stub_reasoner_with_usage(), _noop_delivery(),
        store=store, config=DEFAULT_CONFIG,
    )
    rec = store["history"]()[0]
    assert rec["tokenUsage"] == {"inputTokens": 150, "outputTokens": 42}
    assert rec["errored"] is False


# ---------- token usage absent ----------

def test_token_usage_null_when_reasoner_has_none(tmp_path):
    store = create_local_store(str(tmp_path / "h.json"))
    run_audit(
        _simple_collector(), _stub_reasoner(), _noop_delivery(),
        store=store, config=DEFAULT_CONFIG,
    )
    rec = store["history"]()[0]
    assert rec["tokenUsage"] is None


# ---------- error case (via job.py) ----------

def test_errored_run_logs_error_record(tmp_path):
    from fabric_audit_agent.job import _append_error_record
    import time
    store = create_local_store(str(tmp_path / "h.json"))
    t0 = time.monotonic()
    # Simulate a short delay so duration > 0.
    _append_error_record(store, t0)
    history = store["history"]()
    assert len(history) == 1
    rec = history[0]
    assert rec["errored"] is True
    assert isinstance(rec["durationMs"], int) and rec["durationMs"] >= 0
    assert rec["tokenUsage"] is None
    assert "runAt" in rec


# ---------- Claude reasoner captures token usage ----------

def test_claude_reasoner_captures_token_usage():
    from fabric_audit_agent.adapters.reasoner_claude import create_claude_reasoner

    class _Usage:
        input_tokens = 200
        output_tokens = 55

    class _Resp:
        content = [type("B", (), {"type": "text", "text": "[]"})()]
        usage = _Usage()

    class _Messages:
        def create(self, **kw):
            return _Resp()

    class _Client:
        messages = _Messages()

    reasoner = create_claude_reasoner(_Client())
    # Call with minimal flags to trigger API call.
    facts = {"capacity": {"peakCuPct": 50}}
    flags = [{"type": "capacity.throttle", "resource": "C", "what": "x", "when": "t",
              "evidence": {"peakCuPct": 50}}]
    reasoner["reason"](facts, flags)
    assert reasoner.get("lastUsage") == {"inputTokens": 200, "outputTokens": 55}


def test_claude_reasoner_no_usage_when_api_errors():
    from fabric_audit_agent.adapters.reasoner_claude import create_claude_reasoner

    class _Messages:
        def create(self, **kw):
            raise RuntimeError("network down")

    class _Client:
        messages = _Messages()

    reasoner = create_claude_reasoner(_Client())
    facts = {"capacity": {"peakCuPct": 50}}
    flags = [{"type": "capacity.throttle", "resource": "C", "what": "x", "when": "t",
              "evidence": {"peakCuPct": 50}}]
    reasoner["reason"](facts, flags)
    assert reasoner.get("lastUsage") is None


# ---------- conversation audit log ----------

def test_conversation_audit_log_includes_duration_and_errored(capsys):
    # Import the function from the agent_server package — skip if the app deps aren't installed.
    pytest.importorskip("mlflow")
    from agent_server.agent import _conversation_audit_log
    _conversation_audit_log("hello", [], "world", duration_ms=123, errored=False)
    out = capsys.readouterr().out
    payload = json.loads(out.split("] ", 1)[1])
    assert payload["durationMs"] == 123
    assert payload["errored"] is False


def test_conversation_audit_log_errored_true(capsys):
    pytest.importorskip("mlflow")
    from agent_server.agent import _conversation_audit_log
    _conversation_audit_log("hello", [], "", duration_ms=500, errored=True)
    out = capsys.readouterr().out
    payload = json.loads(out.split("] ", 1)[1])
    assert payload["errored"] is True
    assert payload["durationMs"] == 500
