"""Tests for the Phase-2 agent app: §B1-alt OpenAI adapter + HTTP MCP client.

These cover the new code that has no coverage in the main fabric_audit_agent test suite
because the deployed agent is self-contained (no package import).
"""
import json
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Import helpers — pull the module without triggering mlflow/databricks imports
# ---------------------------------------------------------------------------

def _load_agent_module():
    """Import agent_server.agent with heavy deps stubbed out."""
    import sys, importlib

    # Stub out deploy-only deps that aren't available in the test environment
    for mod in ["mlflow", "mlflow.genai", "mlflow.genai.agent_server",
                "mlflow.types", "mlflow.types.responses",
                "databricks_ai_bridge"]:
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
# HTTP JSON-RPC MCP client
# ---------------------------------------------------------------------------

class TestMcpJsonRpc(unittest.TestCase):
    def _make_resp(self, result=None, sse=False):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        if sse:
            mock_resp.headers = {"content-type": "text/event-stream"}
            mock_resp.text = f"data: {json.dumps({'result': result})}\n"
        else:
            mock_resp.headers = {"content-type": "application/json"}
            mock_resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": result}
        return mock_resp

    def test_tools_list_returns_tool_defs(self):
        tools_payload = {"tools": [
            {"name": "run_audit", "description": "Run audit", "inputSchema": {"type": "object"}},
            {"name": "list_workspaces", "description": "List WS", "inputSchema": {}},
        ]}
        ws = _FakeWs()
        with patch("requests.post", return_value=self._make_resp(tools_payload)) as mock_post:
            # Patch initialize to succeed silently
            call_count = [0]
            real_post = mock_post.side_effect

            def side_effect(url, json=None, **kw):
                call_count[0] += 1
                return self._make_resp({"protocolVersion": "2024-11-05"} if call_count[0] == 1 else tools_payload)

            mock_post.side_effect = side_effect
            tools, dispatch = _agent._mcp_tools_and_dispatch(ws)

        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0]["name"], "run_audit")
        self.assertIn("run_audit", dispatch)
        self.assertIn("list_workspaces", dispatch)

    def test_tool_call_parses_text_content(self):
        tool_result = {"verdict": "optimize", "healthScore": 72}
        call_payload = {"content": [{"type": "text", "text": json.dumps(tool_result)}]}
        ws = _FakeWs()

        with patch("requests.post") as mock_post:
            call_count = [0]
            def side_effect(url, json=None, **kw):
                call_count[0] += 1
                if call_count[0] == 1:  # initialize
                    return self._make_resp({})
                elif call_count[0] == 2:  # tools/list
                    return self._make_resp({"tools": [{"name": "run_audit", "description": "x", "inputSchema": {}}]})
                else:  # tools/call
                    return self._make_resp(call_payload)
            mock_post.side_effect = side_effect

            tools, dispatch = _agent._mcp_tools_and_dispatch(ws)
            result = dispatch["run_audit"]({})

        self.assertEqual(result["verdict"], "optimize")
        self.assertEqual(result["healthScore"], 72)

    def test_sse_response_parsed_correctly(self):
        result_data = {"tools": [{"name": "run_audit", "description": "x", "inputSchema": {}}]}
        ws = _FakeWs()
        with patch("requests.post") as mock_post:
            call_count = [0]
            def side_effect(url, json=None, **kw):
                call_count[0] += 1
                if call_count[0] == 1:
                    return self._make_resp({})
                return self._make_resp(result_data, sse=True)
            mock_post.side_effect = side_effect

            tools, _ = _agent._mcp_tools_and_dispatch(ws)

        self.assertEqual(tools[0]["name"], "run_audit")

    def test_initialize_failure_is_swallowed(self):
        """If initialize fails the client should continue to tools/list."""
        tools_payload = {"tools": [{"name": "run_audit", "description": "x", "inputSchema": {}}]}
        ws = _FakeWs()
        with patch("requests.post") as mock_post:
            call_count = [0]
            def side_effect(url, json=None, **kw):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise Exception("connection refused")
                return self._make_resp(tools_payload)
            mock_post.side_effect = side_effect

            tools, _ = _agent._mcp_tools_and_dispatch(ws)

        self.assertEqual(len(tools), 1)


# ---------------------------------------------------------------------------
# Inlined loop matches the tested original
# ---------------------------------------------------------------------------

class TestInlinedLoopParity(unittest.TestCase):
    """Smoke-test the inlined _run_tool_loop to catch divergence from loop.py."""

    def _make_client(self, responses):
        """Return a fake client that yields successive pre-built responses."""
        idx = [0]
        class _Block:
            def __init__(self, **kw):
                for k, v in kw.items(): setattr(self, k, v)
        class _Resp:
            def __init__(self, content, stop_reason):
                self.content = content
                self.stop_reason = stop_reason
        class _Messages:
            def create(self_inner, **kw):
                r = responses[idx[0]]
                idx[0] += 1
                return r
        class _Client:
            messages = _Messages()
        return _Client(), _Block, _Resp

    def test_direct_answer_no_tool_calls(self):
        client, Block, Resp = self._make_client([
            Resp([Block(type="text", text="The answer is 42.")], "end_turn")
        ])
        result = _agent._run_tool_loop(
            client, model="m", system="s",
            messages=[{"role": "user", "content": "q"}],
            tools=[], dispatch={}, max_steps=3,
        )
        self.assertEqual(result["text"], "The answer is 42.")
        self.assertEqual(result["stoppedReason"], "answer")

    def test_one_tool_call_then_answer(self):
        client, Block, Resp = self._make_client([
            Resp([Block(type="tool_use", id="t1", name="run_audit", input={})], "tool_use"),
            Resp([Block(type="text", text="Audit done.")], "end_turn"),
        ])
        dispatch = {"run_audit": lambda inp: {"verdict": "optimize"}}
        result = _agent._run_tool_loop(
            client, model="m", system="s",
            messages=[{"role": "user", "content": "audit"}],
            tools=[{"name": "run_audit", "description": "", "input_schema": {}}],
            dispatch=dispatch, max_steps=4,
        )
        self.assertEqual(result["text"], "Audit done.")
        self.assertEqual(len(result["toolResults"]), 1)
        self.assertEqual(result["toolResults"][0]["tool"], "run_audit")

    def test_budget_exhaustion(self):
        client, Block, Resp = self._make_client(
            [Resp([Block(type="tool_use", id=f"t{i}", name="run_audit", input={})], "tool_use")
             for i in range(10)]
            + [Resp([Block(type="text", text="done")], "end_turn")]
        )
        dispatch = {"run_audit": lambda inp: {}}
        result = _agent._run_tool_loop(
            client, model="m", system="s",
            messages=[{"role": "user", "content": "q"}],
            tools=[{"name": "run_audit", "description": "", "input_schema": {}}],
            dispatch=dispatch, max_steps=3,
        )
        # At max_steps-1=2, tools are passed; at step 2 (last), tools=[] so model answers
        self.assertEqual(result["stoppedReason"], "answer")


if __name__ == "__main__":
    unittest.main()
