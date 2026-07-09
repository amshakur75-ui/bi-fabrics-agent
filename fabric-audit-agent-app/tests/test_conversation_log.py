"""Tests for the Phase-5.4a conversation-logging seam: `_scrub_secrets` and
`_conversation_audit_log`, wired into `_run` (agent_server/agent.py).

Observability-only: these tests must prove the log line never leaks a tool argument, the full
answer text, or a secret -- including down the failure-isolation (except) path -- and that
`_run`'s return value is unchanged by the new emit.
"""
import json
import re
import types
import unittest
from unittest.mock import MagicMock

from tests.test_agent_server import _load_agent_module, _Block, _Resp


_agent = _load_agent_module()


# ---------------------------------------------------------------------------
# _scrub_secrets
# ---------------------------------------------------------------------------

class TestScrubSecrets(unittest.TestCase):
    def test_sig_masked(self):
        out = _agent._scrub_secrets("nextLink?sig=abcdef123")
        self.assertNotIn("abcdef123", out)

    def test_bearer_masked(self):
        out = _agent._scrub_secrets("Authorization: bearer x")
        self.assertNotIn("bearer x", out)

    def test_client_secret_masked(self):
        out = _agent._scrub_secrets("client_secret=y")
        self.assertNotIn("client_secret=y", out)

    def test_connection_string_accountkey_masked(self):
        """The redact.py \\bkey= allowlist MISSES 'AccountKey=' -- this scrub must not."""
        out = _agent._scrub_secrets("DefaultEndpointsProtocol=https;AccountKey=YWJj==;EndpointSuffix=x")
        self.assertNotIn("YWJj==", out)

    def test_bare_jwt_masked(self):
        jwt = "eyJaaa.eyJbbb.ccc"
        out = _agent._scrub_secrets(f"here is my token {jwt} please use it")
        self.assertNotIn(jwt, out)

    def test_benign_key_value_unchanged(self):
        out = _agent._scrub_secrets("foo=bar")
        self.assertEqual(out, "foo=bar")

    def test_colon_delimited_client_secret_masked(self):
        """A pasted JSON app-registration block uses colon form, not '=' -- must still mask."""
        out = _agent._scrub_secrets('{"client_secret": "abc123XYZ", "client_id": "keep-me"}')
        self.assertNotIn("abc123XYZ", out)
        self.assertIn("client_id", out)  # non-secret name + its value survive

    def test_colon_delimited_api_key_header_masked(self):
        out = _agent._scrub_secrets("x-api-key: SUPERSECRETVALUE")
        self.assertNotIn("SUPERSECRETVALUE", out)

    def test_benign_colon_prose_key_unchanged(self):
        # bare "key:" is deliberately NOT masked (common benign prose/JSON) -- preserves question fidelity.
        out = _agent._scrub_secrets("the key: takeaway is throttling")
        self.assertIn("takeaway", out)

    def test_benign_kql_predicate_unchanged(self):
        out = _agent._scrub_secrets("where Status=200")
        self.assertEqual(out, "where Status=200")

    def test_never_raises_on_non_string(self):
        # Defensive: must not raise even if called with an odd type.
        try:
            _agent._scrub_secrets(None)
        except Exception as exc:  # pragma: no cover - failure path
            self.fail(f"_scrub_secrets raised {exc!r} on None input")


# ---------------------------------------------------------------------------
# _conversation_audit_log
# ---------------------------------------------------------------------------

def _parse_conversation_line(printed):
    """Extract the JSON payload from one printed `[conversation] {...}` line."""
    line = next(l for l in printed.splitlines() if l.startswith("[conversation] "))
    return json.loads(line[len("[conversation] "):])


class TestConversationAuditLog(unittest.TestCase):
    def test_emits_one_line_with_all_six_fields(self):
        trajectory = [{"tool": "spike_events", "input": {}}, {"tool": "run_audit", "input": {}}]
        with self._capture_stdout() as buf:
            _agent._conversation_audit_log("what is throttling?", trajectory, "The verdict is optimize.")
        payload = _parse_conversation_line(buf.getvalue())
        self.assertEqual(payload["tag"], "conversation")
        self.assertIn("ts", payload)
        self.assertEqual(payload["question"], "what is throttling?")
        self.assertEqual(payload["toolsCalled"], ["spike_events", "run_audit"])
        self.assertEqual(payload["toolCount"], 2)
        self.assertIn("abstainedHint", payload)
        self.assertEqual(payload["answerChars"], len("The verdict is optimize."))

    def test_no_tool_call_gives_empty_list_and_zero_count(self):
        with self._capture_stdout() as buf:
            _agent._conversation_audit_log("hello", [], "Hi there.")
        payload = _parse_conversation_line(buf.getvalue())
        self.assertEqual(payload["toolsCalled"], [])
        self.assertEqual(payload["toolCount"], 0)

    def test_abstaining_answer_sets_hint_true(self):
        with self._capture_stdout() as buf:
            _agent._conversation_audit_log("q", [], "I don't have enough data to determine the cause.")
        payload = _parse_conversation_line(buf.getvalue())
        self.assertTrue(payload["abstainedHint"])

    def test_confident_verdict_sets_hint_false(self):
        with self._capture_stdout() as buf:
            _agent._conversation_audit_log("q", [], "The verdict is validated: capacity is oversized.")
        payload = _parse_conversation_line(buf.getvalue())
        self.assertFalse(payload["abstainedHint"])

    def test_tool_input_never_leaked(self):
        trajectory = [{"tool": "spike_events", "input": {"user": "secret@corp.com"}}]
        with self._capture_stdout() as buf:
            _agent._conversation_audit_log("q", trajectory, "answer text")
        printed = buf.getvalue()
        self.assertNotIn("secret@corp.com", printed)
        payload = _parse_conversation_line(printed)
        self.assertEqual(payload["toolsCalled"], ["spike_events"])

    def test_full_answer_never_leaked_only_length(self):
        answer = "The full detailed answer with sensitive-sounding words inside it."
        with self._capture_stdout() as buf:
            _agent._conversation_audit_log("q", [], answer)
        printed = buf.getvalue()
        self.assertNotIn(answer, printed)
        payload = _parse_conversation_line(printed)
        self.assertEqual(payload["answerChars"], len(answer))

    def test_scrub_then_truncate_secret_near_cap_is_masked(self):
        """A secret placed just before the 500-char cap must be scrubbed, not merely cut off
        mid-string (which could still leave part of it readable)."""
        padding = "x" * 470
        question = padding + " client_secret=SEEKRIT"
        with self._capture_stdout() as buf:
            _agent._conversation_audit_log(question, [], "answer")
        payload = _parse_conversation_line(buf.getvalue())
        self.assertNotIn("SEEKRIT", payload["question"])

    def test_overlong_question_truncated_to_cap(self):
        question = "q" * 800
        with self._capture_stdout() as buf:
            _agent._conversation_audit_log(question, [], "answer")
        payload = _parse_conversation_line(buf.getvalue())
        self.assertLessEqual(len(payload["question"]), 500)

    # -- helpers --

    def _capture_stdout(self):
        import contextlib
        import io
        return contextlib.redirect_stdout(io.StringIO())


# ---------------------------------------------------------------------------
# Wiring into _run: emitted once per turn, unchanged return value, and
# failure isolation (the except path must never leak the secret/question).
# ---------------------------------------------------------------------------

class TestRunConversationLogging(unittest.IsolatedAsyncioTestCase):
    def _make_client(self, responses):
        idx = [0]

        class _Messages:
            def create(self_inner, **kw):
                r = responses[idx[0]]
                idx[0] += 1
                return r

        class _Client:
            messages = _Messages()

        return _Client()

    async def _fake_tools(self, ws):
        async def handler(inp):
            return {"verdict": "ok"}
        return ([{"name": "spike_events", "description": "", "input_schema": {}}],
                {"spike_events": handler})

    def _request(self, text):
        return types.SimpleNamespace(input=[{"role": "user", "content": text}])

    async def test_run_emits_one_conversation_line_and_returns_loop_result_unchanged(self):
        client = self._make_client([
            _Resp([_Block(type="tool_use", id="t1", name="spike_events", input={})], "tool_use"),
            _Resp([_Block(type="text", text="The verdict is optimize.")], "end_turn"),
        ])
        request = self._request("what's driving the spike?")

        from unittest.mock import patch
        import io
        import contextlib

        with patch.object(_agent, "_mcp_tools_and_dispatch", new=self._fake_tools), \
             patch.object(_agent, "_build_claude_client", return_value=client):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                result = await _agent._run(request)

        lines = [l for l in buf.getvalue().splitlines() if l.startswith("[conversation] ")]
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0][len("[conversation] "):])
        self.assertEqual(payload["toolsCalled"], ["spike_events"])
        self.assertEqual(payload["toolCount"], 1)
        self.assertEqual(payload["question"], "what's driving the spike?")
        self.assertEqual(result["text"], "The verdict is optimize.")
        self.assertEqual(result["trajectory"], [{"tool": "spike_events", "input": {}}])

    async def test_run_returns_answer_even_if_logging_raises_and_does_not_re_leak(self):
        client = self._make_client([
            _Resp([_Block(type="text", text="answer with SEEKRIT_TOKEN inside")], "end_turn"),
        ])
        secret_question = "here is my client_secret=SEEKRIT_TOKEN please help"
        request = self._request(secret_question)

        from unittest.mock import patch
        import io
        import contextlib

        def _boom(*a, **kw):
            raise RuntimeError("logging exploded")

        with patch.object(_agent, "_mcp_tools_and_dispatch", new=self._fake_tools), \
             patch.object(_agent, "_build_claude_client", return_value=client), \
             patch.object(_agent, "_conversation_audit_log", side_effect=_boom):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                result = await _agent._run(request)

        printed = buf.getvalue()
        self.assertNotIn("SEEKRIT_TOKEN", printed)
        self.assertNotIn(secret_question, printed)
        self.assertEqual(result["text"], "answer with SEEKRIT_TOKEN inside")


if __name__ == "__main__":
    unittest.main()
