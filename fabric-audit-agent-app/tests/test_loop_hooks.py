"""Loop hooks (plan 3.10 / tightening 24b) — the three shared hooks and the TWIN-EQUIVALENCE
guarantee: the async ``agent.py::_run_tool_loop`` and the sync ``loop.py::run_tool_loop`` must
expose identical hook behaviour. The hooks live once in ``agent_server.loop_hooks`` and both
loops call them at the same two seams, so this test drives the SAME scripted scenario through
both loops and asserts the same observable outcome."""
import asyncio
import importlib.util
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

from agent_server.loop import run_tool_loop
from agent_server import loop_hooks
from agent_server.loop_hooks import (
    pretool_pbi_usage_redirect,
    post_tool_result,
    normalize_executing_user_display,
)
from agent_server.scripted_client import Block as _B, Message as _M, ScriptedClient


# An improvised (hand-authored, non-bracketed) EventText filter on PowerBIDatasetsWorkspace —
# exactly what CORRECT007 / hook (c) must catch and redirect.
_IMPROVISED_PBI_KQL = (
    "PowerBIDatasetsWorkspace\n| where TimeGenerated > ago(1d)\n"
    '| where EventText has "invoice"\n| take 5'
)
# An authoritative bracketed DAX pattern (what the resolver/builder emits) — must NOT redirect.
_AUTHORITATIVE_PBI_KQL = (
    "PowerBIDatasetsWorkspace\n| where TimeGenerated > ago(1d)\n"
    "| where EventText contains \"'Sales'[Invoice Quantity]\"\n| take 5"
)


# ── Hook (c): PBI-usage redirect — pure function ────────────────────────────────────
def test_redirect_fires_on_improvised_pbi_filter():
    r = pretool_pbi_usage_redirect("run_kql", {"engine": "la", "kql": _IMPROVISED_PBI_KQL})
    assert r is not None and r["redirected"] is True and r["kql"] == ""
    assert "field" in r["message"].lower()


def test_redirect_skips_authoritative_bracketed_query():
    assert pretool_pbi_usage_redirect("run_kql", {"kql": _AUTHORITATIVE_PBI_KQL}) is None


def test_redirect_skips_non_pbi_query():
    assert pretool_pbi_usage_redirect(
        "run_kql", {"kql": "CapacityEvents | where cap == 'x' | take 5"}) is None


def test_redirect_skips_non_query_tools_and_empty():
    assert pretool_pbi_usage_redirect("run_audit", {"kql": _IMPROVISED_PBI_KQL}) is None
    assert pretool_pbi_usage_redirect("run_kql", {}) is None


# ── Hooks (a)+(b): post-tool-result — pure function ─────────────────────────────────
def test_post_hook_adds_analysis_nudge_on_rows():
    res = post_tool_result("run_kql", {}, {"rows": [{"a": 1}], "source": "live"})
    assert "analysisNudge" in res


def test_post_hook_no_nudge_on_error_or_empty():
    assert "analysisNudge" not in post_tool_result("run_kql", {}, {"error": "boom"})
    assert "analysisNudge" not in post_tool_result("run_kql", {}, {"rows": []})


def test_post_hook_normalizes_executing_user_rows():
    res = post_tool_result(
        "run_kql", {}, {"rows": [{"ExecutingUser": "jdoe", "QueryCount": 3},
                                 {"ExecutingUser": "a@newellco.com"},
                                 {"ExecutingUser": ""}]})
    users = [r["ExecutingUser"] for r in res["rows"]]
    assert users == ["jdoe@newellco.com", "a@newellco.com", ""]


def test_post_hook_ignores_non_execution_tool_but_still_normalizes():
    # identity normalization is defense-in-depth on ANY tool's rows; the analysis nudge is not.
    res = post_tool_result("list_workspaces", {}, {"rows": [{"ExecutingUser": "bob"}]})
    assert res["rows"][0]["ExecutingUser"] == "bob@newellco.com"
    assert "analysisNudge" not in res


def test_normalize_executing_user_display_contract():
    assert normalize_executing_user_display(None) == ""
    assert normalize_executing_user_display("  ") == ""
    assert normalize_executing_user_display("jdoe") == "jdoe@newellco.com"
    assert normalize_executing_user_display("j@x.com") == "j@x.com"


# ── The async twin, loaded with heavy deploy-deps stubbed (mirrors test_agent_server.py) ──
def _load_async_run_tool_loop():
    for mod in ["mlflow", "mlflow.genai", "mlflow.genai.agent_server", "mlflow.types",
                "mlflow.types.responses", "databricks_ai_bridge", "databricks_mcp",
                "databricks", "databricks.sdk"]:
        sys.modules.setdefault(mod, MagicMock())
    ags = sys.modules["mlflow.genai.agent_server"]
    ags.invoke = lambda *a, **kw: (lambda f: f)
    ags.stream = lambda *a, **kw: (lambda f: f)
    spec = importlib.util.spec_from_file_location(
        "agent_server.agent", pathlib.Path(__file__).parent.parent / "agent_server" / "agent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._run_tool_loop


def _sync_scenario(kql, handler_return):
    """Drive the SYNC loop with one run_kql tool call then an answer; return
    (handler_call_count, toolResults, tool_result_content_string)."""
    calls = []

    def h(inp):
        calls.append(inp)
        return dict(handler_return)

    scripted = [_M([_B("tool_use", id="t1", name="run_kql", input={"engine": "la", "kql": kql})], "tool_use"),
                _M([_B("text", text="done")], "end_turn")]
    client = ScriptedClient(scripted)
    out = run_tool_loop(client, model="m", system="s",
                        messages=[{"role": "user", "content": "who used invoice last month"}],
                        tools=[{"name": "run_kql"}], dispatch={"run_kql": h}, max_steps=4)
    tr_msg = next((m for m in client.calls[1]["messages"]
                   if m["role"] == "user" and isinstance(m["content"], list)), None)
    content = tr_msg["content"][0]["content"] if tr_msg else ""
    return len(calls), out["toolResults"], content


def _async_scenario(kql, handler_return):
    """Same scenario through the ASYNC twin."""
    run = _load_async_run_tool_loop()
    calls = []

    async def h(inp):
        calls.append(inp)
        return dict(handler_return)

    class _Msgs:
        def __init__(self):
            self._i = 0
            self._resp = [
                _B("tool_use", id="t1", name="run_kql", input={"engine": "la", "kql": kql}),
                "answer",
            ]

        def create(self, **kw):
            i, self._i = self._i, self._i + 1
            if i == 0:
                return type("R", (), {"content": [self._resp[0]], "stop_reason": "tool_use"})()
            return type("R", (), {"content": [_B("text", text="done")], "stop_reason": "end_turn"})()

    client = type("C", (), {"messages": _Msgs()})()
    captured = {}

    async def on_tool(name, inp):
        pass

    out = asyncio.run(run(client, model="m", system="s",
                          messages=[{"role": "user", "content": "who used invoice last month"}],
                          tools=[{"name": "run_kql"}], dispatch={"run_kql": h}, max_steps=4,
                          on_tool=on_tool))
    return len(calls), out["toolResults"]


def test_twins_both_redirect_improvised_pbi_query_and_skip_handler():
    """The critical hook (c): a hand-authored PBI-usage query is redirected BEFORE execution in
    BOTH loops — the handler is never invoked and the model is told to use the resolver."""
    sync_calls, sync_tr, sync_content = _sync_scenario(_IMPROVISED_PBI_KQL, {"rows": [{"x": 1}]})
    async_calls, _ = _async_scenario(_IMPROVISED_PBI_KQL, {"rows": [{"x": 1}]})
    assert sync_calls == 0                    # sync twin: handler skipped
    assert async_calls == 0                   # async twin: handler skipped — identical behaviour
    assert "resolver" in sync_content.lower() and "discarded" in sync_content.lower()


def test_twins_both_execute_and_postprocess_clean_query():
    """A clean (non-PBI) query executes in both twins and gets the SAME post-hook treatment:
    the analysis nudge + ExecutingUser normalization applied at the structured-row layer."""
    clean = "CapacityEvents | where cap == 'x' | summarize c = count() by ExecutingUser | take 5"
    handler_return = {"rows": [{"ExecutingUser": "jdoe", "c": 4}], "source": "live"}
    sync_calls, sync_tr, _ = _sync_scenario(clean, handler_return)
    async_calls, async_tr = _async_scenario(clean, handler_return)
    assert sync_calls == 1 and async_calls == 1
    for tr in (sync_tr, async_tr):
        result = tr[0]["result"]
        assert result["rows"][0]["ExecutingUser"] == "jdoe@newellco.com"   # hook (b)
        assert "analysisNudge" in result                                    # hook (a)


def test_twins_preserve_documented_divergences():
    """The 3 intentional twin divergences must survive the hook edits: agent.py's toolResults
    element carries callId+input, loop.py's does not."""
    clean = "CapacityEvents | take 5"
    _, sync_tr, _ = _sync_scenario(clean, {"rows": [{"x": 1}]})
    _, async_tr = _async_scenario(clean, {"rows": [{"x": 1}]})
    assert set(sync_tr[0].keys()) == {"tool", "result"}                      # sync shape
    assert set(async_tr[0].keys()) == {"tool", "callId", "input", "result"}  # async shape
