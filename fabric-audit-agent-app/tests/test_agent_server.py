"""Tests for the Phase-2 agent app: §B1-alt OpenAI adapter + HTTP MCP client.

These cover the new code that has no coverage in the main fabric_audit_agent test suite
because the deployed agent is self-contained (no package import).
"""
import json
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Import helpers — pull the module without triggering mlflow/databricks imports
# ---------------------------------------------------------------------------

def _load_agent_module():
    """Import agent_server.agent with heavy deps stubbed out."""
    import sys, importlib

    # Stub out deploy-only deps that aren't available in the test environment
    for mod in ["mlflow", "mlflow.genai", "mlflow.genai.agent_server",
                "mlflow.types", "mlflow.types.responses",
                "databricks_ai_bridge", "databricks_mcp",
                "databricks", "databricks.sdk"]:
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

    # Stub the decorator factories so @invoke()/@stream() are no-ops
    ags = sys.modules["mlflow.genai.agent_server"]
    ags.invoke = lambda *a, **kw: (lambda f: f)
    ags.stream = lambda *a, **kw: (lambda f: f)

    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "agent_server.agent",
        pathlib.Path(__file__).parent.parent / "agent_server" / "agent.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_agent = _load_agent_module()


# ---------------------------------------------------------------------------
# §B1-alt adapter: OpenAI chat-completions → Anthropic Messages shape
# ---------------------------------------------------------------------------

class _FakeWs:
    class config:
        host = "https://fake.databricks.net"
        token = "fake-token"

        @staticmethod
        def authenticate():
            return {"Authorization": "Bearer fake-token"}


class TestB1AltAdapter(unittest.TestCase):
    def _get_client(self):
        return _agent._build_claude_client(_FakeWs())

    def _mock_post(self, content_text=None, tool_calls=None, finish_reason="stop"):
        msg = {}
        if content_text:
            msg["content"] = content_text
        if tool_calls:
            msg["tool_calls"] = tool_calls
        resp_data = {"choices": [{"message": msg, "finish_reason": finish_reason}]}

        mock_resp = MagicMock()
        mock_resp.json.return_value = resp_data
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def test_text_response_maps_to_text_block(self):
        client = self._get_client()
        with patch("requests.post", return_value=self._mock_post(content_text="Hello")):
            resp = client.messages.create(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
            )
        self.assertEqual(resp.stop_reason, "end_turn")
        self.assertEqual(len(resp.content), 1)
        self.assertEqual(resp.content[0].type, "text")
        self.assertEqual(resp.content[0].text, "Hello")

    def test_tool_calls_maps_to_tool_use_blocks(self):
        tool_calls = [{
            "id": "call_abc",
            "type": "function",
            "function": {"name": "run_audit", "arguments": "{}"},
        }]
        client = self._get_client()
        with patch("requests.post",
                   return_value=self._mock_post(tool_calls=tool_calls, finish_reason="tool_calls")):
            resp = client.messages.create(
                messages=[{"role": "user", "content": "audit now"}],
                tools=[{"name": "run_audit", "description": "x", "input_schema": {}}],
            )
        self.assertEqual(resp.stop_reason, "tool_use")
        self.assertEqual(len(resp.content), 1)
        self.assertEqual(resp.content[0].type, "tool_use")
        self.assertEqual(resp.content[0].name, "run_audit")
        self.assertEqual(resp.content[0].id, "call_abc")
        self.assertEqual(resp.content[0].input, {})

    def test_finish_reason_mapping(self):
        client = self._get_client()
        cases = [
            ("stop", "end_turn"),
            ("tool_calls", "tool_use"),
            ("length", "max_tokens"),
            ("unknown_val", "end_turn"),
        ]
        for finish, expected_stop in cases:
            with patch("requests.post",
                       return_value=self._mock_post(content_text="x", finish_reason=finish)):
                resp = client.messages.create(messages=[{"role":"user","content":"x"}], tools=[])
            self.assertEqual(resp.stop_reason, expected_stop, f"finish_reason={finish!r}")

    def test_uses_authenticate_headers_not_bare_token(self):
        """ws.config.token is PAT-only and empty under SP/OAuth auth; the client must send
        whatever ws.config.authenticate() returns instead of hand-building 'Bearer <token>'."""
        client = self._get_client()
        captured = {}

        def fake_post(url, json=None, headers=None, **kw):
            captured["headers"] = headers
            return self._mock_post(content_text="ok")

        with patch("requests.post", side_effect=fake_post):
            client.messages.create(messages=[{"role": "user", "content": "hi"}], tools=[])

        self.assertEqual(captured["headers"]["Authorization"], "Bearer fake-token")

    def test_tool_result_messages_sent_as_tool_role(self):
        """tool_result blocks in user messages must become role=tool messages for OpenAI."""
        client = self._get_client()
        captured = {}

        def fake_post(url, json=None, headers=None, **kw):
            captured["body"] = json
            return self._mock_post(content_text="done")

        with patch("requests.post", side_effect=fake_post):
            client.messages.create(
                messages=[
                    {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": "call_1",
                         "content": '{"verdict":"ok"}'}
                    ]}
                ],
                tools=[],
            )

        oai_msgs = captured["body"]["messages"]
        tool_msg = next(m for m in oai_msgs if m["role"] == "tool")
        self.assertEqual(tool_msg["tool_call_id"], "call_1")
        self.assertIn("verdict", tool_msg["content"])

    def test_tools_converted_to_openai_function_format(self):
        client = self._get_client()
        captured = {}

        def fake_post(url, json=None, **kw):
            captured["body"] = json
            return self._mock_post(content_text="ok")

        with patch("requests.post", side_effect=fake_post):
            client.messages.create(
                messages=[{"role": "user", "content": "go"}],
                tools=[{"name": "run_audit", "description": "run it",
                         "input_schema": {"type": "object", "properties": {}}}],
            )

        oai_tools = captured["body"]["tools"]
        self.assertEqual(len(oai_tools), 1)
        self.assertEqual(oai_tools[0]["type"], "function")
        self.assertEqual(oai_tools[0]["function"]["name"], "run_audit")
        self.assertIn("parameters", oai_tools[0]["function"])


# ---------------------------------------------------------------------------
# MCP tool sourcing — via databricks_mcp.DatabricksMCPClient (app-to-app OAuth)
# ---------------------------------------------------------------------------

def _reset_state():
    """Clear the module TTL cache so each test's mocks are actually exercised."""
    _agent._STATE.update({"ws": None, "tools": None, "dispatch": None, "tools_at": 0.0})


class TestMcpToolsAndDispatch(unittest.IsolatedAsyncioTestCase):
    """_mcp_tools_and_dispatch uses the async alist_tools/acall_tool variants —
    the sync ones call asyncio.run() internally, which breaks inside the
    already-running event loop our async handlers run under."""

    def setUp(self):
        _reset_state()

    def _make_tool(self, name, description="", input_schema=None):
        t = MagicMock()
        t.name = name
        t.description = description
        t.inputSchema = input_schema if input_schema is not None else {}
        return t

    def _make_call_result(self, text):
        content_item = MagicMock()
        content_item.text = text
        result = MagicMock()
        result.content = [content_item]
        return result

    async def test_tools_list_returns_tool_defs(self):
        ws = _FakeWs()
        mock_client = MagicMock()
        mock_client.alist_tools = AsyncMock(return_value=[
            self._make_tool("run_audit", "Run audit", {"type": "object"}),
            self._make_tool("list_workspaces", "List WS", {}),
        ])
        with patch("databricks_mcp.DatabricksMCPClient", return_value=mock_client):
            tools, dispatch = await _agent._mcp_tools_and_dispatch(ws)

        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0]["name"], "run_audit")
        self.assertEqual(tools[0]["input_schema"], {"type": "object"})
        self.assertIn("run_audit", dispatch)
        self.assertIn("list_workspaces", dispatch)

    async def test_client_constructed_with_server_url_and_workspace_client(self):
        ws = _FakeWs()
        mock_client = MagicMock()
        mock_client.alist_tools = AsyncMock(return_value=[])
        with patch("databricks_mcp.DatabricksMCPClient", return_value=mock_client) as mock_cls:
            await _agent._mcp_tools_and_dispatch(ws)

        mock_cls.assert_called_once_with(server_url=_agent._MCP_URL, workspace_client=ws)

    async def test_tool_call_parses_json_text_content(self):
        ws = _FakeWs()
        mock_client = MagicMock()
        mock_client.alist_tools = AsyncMock(return_value=[self._make_tool("run_audit")])
        mock_client.acall_tool = AsyncMock(return_value=self._make_call_result(
            json.dumps({"verdict": "optimize", "healthScore": 72})))

        with patch("databricks_mcp.DatabricksMCPClient", return_value=mock_client):
            _, dispatch = await _agent._mcp_tools_and_dispatch(ws)
            result = await dispatch["run_audit"]({})

        self.assertEqual(result["verdict"], "optimize")
        self.assertEqual(result["healthScore"], 72)
        mock_client.acall_tool.assert_called_once_with("run_audit", {})

    async def test_tool_call_falls_back_to_raw_text_on_non_json(self):
        ws = _FakeWs()
        mock_client = MagicMock()
        mock_client.alist_tools = AsyncMock(return_value=[self._make_tool("run_audit")])
        mock_client.acall_tool = AsyncMock(return_value=self._make_call_result("plain text result"))

        with patch("databricks_mcp.DatabricksMCPClient", return_value=mock_client):
            _, dispatch = await _agent._mcp_tools_and_dispatch(ws)
            result = await dispatch["run_audit"]({})

        self.assertEqual(result, "plain text result")


# ---------------------------------------------------------------------------
# Inlined loop matches the tested original
# ---------------------------------------------------------------------------

def _async_handler(value):
    """dispatch handlers are async now — _run_tool_loop awaits them."""
    async def _inner(_inp):
        return value
    return _inner


class _Block:
    def __init__(self, **kw):
        for k, v in kw.items(): setattr(self, k, v)


class _Resp:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


class TestInlinedLoopParity(unittest.IsolatedAsyncioTestCase):
    """Smoke-test the inlined _run_tool_loop to catch divergence from loop.py."""

    def _make_client(self, responses):
        """Return a fake client that yields successive pre-built responses."""
        idx = [0]
        class _Messages:
            def create(self_inner, **kw):
                r = responses[idx[0]]
                idx[0] += 1
                return r
        class _Client:
            messages = _Messages()
        return _Client()

    async def test_direct_answer_no_tool_calls(self):
        client = self._make_client([
            _Resp([_Block(type="text", text="The answer is 42.")], "end_turn")
        ])
        result = await _agent._run_tool_loop(
            client, model="m", system="s",
            messages=[{"role": "user", "content": "q"}],
            tools=[], dispatch={}, max_steps=3,
        )
        self.assertEqual(result["text"], "The answer is 42.")
        self.assertEqual(result["stoppedReason"], "answer")

    async def test_one_tool_call_then_answer(self):
        client = self._make_client([
            _Resp([_Block(type="tool_use", id="t1", name="run_audit", input={})], "tool_use"),
            _Resp([_Block(type="text", text="Audit done.")], "end_turn"),
        ])
        dispatch = {"run_audit": _async_handler({"verdict": "optimize"})}
        result = await _agent._run_tool_loop(
            client, model="m", system="s",
            messages=[{"role": "user", "content": "audit"}],
            tools=[{"name": "run_audit", "description": "", "input_schema": {}}],
            dispatch=dispatch, max_steps=4,
        )
        self.assertEqual(result["text"], "Audit done.")
        self.assertEqual(len(result["toolResults"]), 1)
        self.assertEqual(result["toolResults"][0]["tool"], "run_audit")

    async def test_forced_final_step_carries_budget_nudge(self):
        """Parity with loop.py: the forced-answer step must inject the budget-exhausted
        instruction (observed live: without it the model narrated its next tool call)."""
        seen = []
        responses = [
            _Resp([_Block(type="tool_use", id="t1", name="run_audit", input={"n": 1})], "tool_use"),
            _Resp([_Block(type="tool_use", id="t2", name="run_audit", input={"n": 2})], "tool_use"),
            _Resp([_Block(type="text", text="final")], "end_turn"),
        ]
        idx = [0]

        class _Messages:
            def create(self_inner, model=None, max_tokens=None, system=None, messages=None, tools=None):
                seen.append({"messages": list(messages), "tools": list(tools or [])})
                r = responses[idx[0]]; idx[0] += 1
                return r

        client = types.SimpleNamespace(messages=_Messages())
        dispatch = {"run_audit": _async_handler({})}
        result = await _agent._run_tool_loop(
            client, model="m", system="s",
            messages=[{"role": "user", "content": "q"}],
            tools=[{"name": "run_audit", "description": "", "input_schema": {}}],
            dispatch=dispatch, max_steps=3,
        )
        self.assertEqual(result["text"], "final")
        self.assertEqual(seen[-1]["tools"], [])
        nudge = seen[-1]["messages"][-1]
        self.assertEqual(nudge["role"], "user")
        self.assertIn("budget exhausted", nudge["content"].lower())

    async def test_budget_exhaustion(self):
        # The fake client replays this list regardless of the tools= it was called
        # with, so even though _run_tool_loop passes tools=[] on the final step, the
        # model "keeps calling tools" for all max_steps iterations — the loop must
        # exhaust its budget and report "budget", not fabricate an answer.
        client = self._make_client(
            [_Resp([_Block(type="tool_use", id=f"t{i}", name="run_audit", input={})], "tool_use")
             for i in range(10)]
            + [_Resp([_Block(type="text", text="done")], "end_turn")]
        )
        dispatch = {"run_audit": _async_handler({})}
        result = await _agent._run_tool_loop(
            client, model="m", system="s",
            messages=[{"role": "user", "content": "q"}],
            tools=[{"name": "run_audit", "description": "", "input_schema": {}}],
            dispatch=dispatch, max_steps=3,
        )
        self.assertEqual(result["stoppedReason"], "budget")


# ---------------------------------------------------------------------------
# _messages_from_request — real Responses-API clients send content blocks as
# parsed Pydantic objects, not plain dicts. This must not silently drop them.
# ---------------------------------------------------------------------------

class _FakeContentBlock:
    """Stands in for mlflow's ResponseInputTextParam -- has .text, no .get()."""
    def __init__(self, text):
        self.text = text


class _FakeInputItem:
    """Stands in for mlflow's Message -- attribute access, no .get()."""
    def __init__(self, role, content):
        self.role = role
        self.content = content


class TestMessagesFromRequest(unittest.TestCase):
    def test_plain_string_content(self):
        request = types.SimpleNamespace(input=[{"role": "user", "content": "hi there"}])
        msgs = _agent._messages_from_request(request)
        self.assertEqual(msgs, [{"role": "user", "content": "hi there"}])

    def test_list_of_dict_content_blocks(self):
        request = types.SimpleNamespace(input=[
            {"role": "user", "content": [{"type": "input_text", "text": "hi there"}]}
        ])
        msgs = _agent._messages_from_request(request)
        self.assertEqual(msgs, [{"role": "user", "content": "hi there"}])

    def test_list_of_object_content_blocks_not_dropped(self):
        """Regression: content blocks parsed as ResponseInputTextParam objects (attribute
        access, not dict) must not be silently filtered out, which previously sent Claude
        an empty message and got a 400 Bad Request from the serving endpoint."""
        item = _FakeInputItem(role="user", content=[_FakeContentBlock("hi there")])
        request = types.SimpleNamespace(input=[item])
        msgs = _agent._messages_from_request(request)
        self.assertEqual(msgs, [{"role": "user", "content": "hi there"}])

    def test_mixed_dict_and_object_content_blocks(self):
        item = _FakeInputItem(role="user", content=[
            {"type": "input_text", "text": "part one"},
            _FakeContentBlock("part two"),
        ])
        request = types.SimpleNamespace(input=[item])
        msgs = _agent._messages_from_request(request)
        self.assertEqual(msgs, [{"role": "user", "content": "part one part two"}])


# ---------------------------------------------------------------------------
# Hardening batch: endpoint timeout/retry, TTL caches, streaming progress
# ---------------------------------------------------------------------------

class TestClaudePostHardening(unittest.TestCase):
    def setUp(self):
        _reset_state()

    def _ok_resp(self, text="ok"):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
        r.raise_for_status = MagicMock()
        return r

    def test_post_includes_timeout(self):
        """A hung serving-endpoint call must not outlive the request -- timeout is mandatory."""
        captured = {}

        def fake_post(url, json=None, headers=None, **kw):
            captured.update(kw)
            return self._ok_resp()

        client = _agent._build_claude_client(_FakeWs())
        with patch("requests.post", side_effect=fake_post):
            client.messages.create(messages=[{"role": "user", "content": "hi"}], tools=[])
        assert captured.get("timeout") is not None

    def test_post_retries_once_on_transient_5xx(self):
        bad = MagicMock()
        bad.status_code = 503
        calls = {"n": 0}

        def fake_post(url, json=None, headers=None, **kw):
            calls["n"] += 1
            return bad if calls["n"] == 1 else self._ok_resp("recovered")

        client = _agent._build_claude_client(_FakeWs())
        with patch("requests.post", side_effect=fake_post), patch("time.sleep"):
            resp = client.messages.create(messages=[{"role": "user", "content": "hi"}], tools=[])
        self.assertEqual(calls["n"], 2)
        self.assertEqual(resp.content[0].text, "recovered")


class TestToolsCache(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _reset_state()

    async def test_tools_list_cached_within_ttl(self):
        """The MCP client build + tools/list round-trip must happen once per TTL, not per message."""
        ws = _FakeWs()
        t = MagicMock(); t.name = "run_audit"; t.description = ""; t.inputSchema = {}
        mock_client = MagicMock()
        mock_client.alist_tools = AsyncMock(return_value=[t])
        with patch("databricks_mcp.DatabricksMCPClient", return_value=mock_client) as mock_cls:
            tools1, _ = await _agent._mcp_tools_and_dispatch(ws)
            tools2, _ = await _agent._mcp_tools_and_dispatch(ws)
        self.assertEqual(mock_cls.call_count, 1)      # second call served from cache
        self.assertEqual(tools1, tools2)


class TestStreamingProgress(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _reset_state()

    async def test_stream_yields_progress_then_final(self):
        """One progress event per tool call, then the final answer -- not silence-then-answer."""
        blocks_tool = [types.SimpleNamespace(type="tool_use", id="t1", name="run_audit", input={})]
        blocks_text = [types.SimpleNamespace(type="text", text="Audit done.")]
        responses = [types.SimpleNamespace(content=blocks_tool, stop_reason="tool_use"),
                     types.SimpleNamespace(content=blocks_text, stop_reason="end_turn")]
        idx = {"i": 0}

        class _Messages:
            def create(self_inner, **kw):
                r = responses[idx["i"]]; idx["i"] += 1
                return r

        fake_client = types.SimpleNamespace(messages=_Messages())

        async def fake_tools(ws):
            async def handler(inp):
                return {"verdict": "ok"}
            return ([{"name": "run_audit", "description": "", "input_schema": {}}],
                    {"run_audit": handler})

        _agent.create_text_output_item.reset_mock()
        request = types.SimpleNamespace(input=[{"role": "user", "content": "audit"}])
        with patch.object(_agent, "_mcp_tools_and_dispatch", new=fake_tools), \
             patch.object(_agent, "_build_claude_client", return_value=fake_client):
            events = [e async for e in _agent.stream_handler(request)]

        self.assertEqual(len(events), 2)   # 1 progress + 1 final
        texts = [c.kwargs.get("text", "") for c in _agent.create_text_output_item.call_args_list]
        self.assertTrue(any("run_audit" in t for t in texts[:-1]))   # progress names the tool
        self.assertEqual(texts[-1], "Audit done.")                   # final answer last

    async def test_stream_failure_ends_with_honest_message_not_broken_stream(self):
        """A raised exception mid-run would abort the SSE stream and the chat UI shows a
        broken/blank response -- the stream must end with a readable failure event instead."""
        async def exploding_tools(ws):
            raise RuntimeError("MCP unreachable")

        _agent.create_text_output_item.reset_mock()
        request = types.SimpleNamespace(input=[{"role": "user", "content": "audit"}])
        with patch.object(_agent, "_mcp_tools_and_dispatch", new=exploding_tools):
            events = [e async for e in _agent.stream_handler(request)]   # must NOT raise

        self.assertEqual(len(events), 1)
        final = _agent.create_text_output_item.call_args_list[-1].kwargs.get("text", "")
        self.assertIn("MCP unreachable", final)
        self.assertIn("read-only", final)   # reassures nothing was modified


if __name__ == "__main__":
    unittest.main()
