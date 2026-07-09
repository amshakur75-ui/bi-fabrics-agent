"""Tests for `_progress_text` (Phase 5.1, Task 2) -- humanized, plain-English progress lines.

`_progress_text(name, inp)` feeds the streaming handler's per-tool-call progress events (see
`stream_handler` in `agent_server/agent.py`). It must never leak the raw tool name or a JSON
blob to the user -- only a plain phrase plus an optional, whitelisted scope hint.

Uses the same module-executing import helper as `test_agent_server.py` (heavy deploy-only deps
stubbed out) because we need the live function object, not just source text -- unlike
`test_prompt_parity.py`, which intentionally reads raw source.
"""
import unittest
from unittest.mock import MagicMock


def _load_agent_module():
    """Import agent_server.agent with heavy deps stubbed out."""
    import sys, importlib

    for mod in ["mlflow", "mlflow.genai", "mlflow.genai.agent_server",
                "mlflow.types", "mlflow.types.responses",
                "databricks_ai_bridge", "databricks_mcp",
                "databricks", "databricks.sdk"]:
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

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
_progress_text = _agent._progress_text


# The 18 tools confirmed against tools.py::create_tool_definitions, and their exact mapped
# phrase per the finalized user wording (see docs/superpowers/specs/2026-07-08-personality-ux-
# design.md, "_progress_text design").
_PHRASE_CASES = [
    ("run_audit", "running the capacity audit"),
    ("list_workspaces", "listing the workspaces"),
    ("user_activity", "looking into that user's activity"),
    ("investigate_user", "looking into that user's activity"),
    ("user_timeline", "looking into that user's activity"),
    ("user_spike_history", "looking into that user's activity"),
    ("investigate_capacity_spike", "checking events with unusual spikes"),
    ("spike_events", "checking events with unusual spikes"),
    ("raw_events", "pulling the raw event stream"),
    ("capacity_patterns", "analyzing capacity patterns"),
    ("capacity_diagnostics", "analyzing capacity patterns"),
    ("describe_source", "checking what the data source contains"),
    ("sample_events", "checking what the data source contains"),
    ("diagnose", "working through the diagnosis"),
    ("analyze_dax", "reviewing the DAX"),
    ("whats_changed", "comparing against the last run"),
    ("run_kql", "running a read-only query"),
    ("query_library", "checking the query library"),
]


class TestPhraseMap(unittest.TestCase):
    def test_all_18_tools_map_to_exact_phrase(self):
        self.assertEqual(len(_PHRASE_CASES), 18, "expected exactly 18 tool names under test")
        for name, phrase in _PHRASE_CASES:
            with self.subTest(name=name):
                self.assertEqual(_progress_text(name, None), f"\U0001F50E {phrase}")

    def test_unmapped_name_gets_generic_phrase(self):
        self.assertEqual(_progress_text("some_future_tool", None), "\U0001F50E working on it…")

    def test_leading_glyph_retained(self):
        text = _progress_text("run_audit", None)
        self.assertTrue(text.startswith("\U0001F50E "))


class TestNoLeak(unittest.TestCase):
    def test_no_tool_name_or_json_with_args_present(self):
        for name, _ in _PHRASE_CASES:
            with self.subTest(name=name):
                inp = {"topN": 10, "workspace_id": "abc-123", "extra": {"nested": 1}}
                text = _progress_text(name, inp)
                self.assertNotIn(name, text)
                self.assertNotIn("{", text)
                self.assertNotIn("}", text)

    def test_unmapped_name_also_leaks_nothing(self):
        inp = {"user": "bob@co", "raw": "irrelevant"}
        text = _progress_text("totally_unknown_tool_xyz", inp)
        self.assertNotIn("totally_unknown_tool_xyz", text)
        self.assertNotIn("{", text)
        self.assertNotIn("}", text)


class TestInputEdgeCases(unittest.TestCase):
    def test_inp_none_no_hint_no_error(self):
        self.assertEqual(_progress_text("run_audit", None), "\U0001F50E running the capacity audit")

    def test_inp_empty_dict_no_hint(self):
        self.assertEqual(_progress_text("run_audit", {}), "\U0001F50E running the capacity audit")

    def test_inp_non_dict_no_hint_no_error(self):
        for bad_inp in ["a string", 42, ["list", "of", "things"], object()]:
            with self.subTest(bad_inp=bad_inp):
                text = _progress_text("run_audit", bad_inp)
                self.assertEqual(text, "\U0001F50E running the capacity audit")


class TestScopeHints(unittest.TestCase):
    def test_topN_renders_top_n(self):
        self.assertEqual(
            _progress_text("spike_events", {"topN": 25}),
            "\U0001F50E checking events with unusual spikes (top 25)",
        )

    def test_user_renders_for_user(self):
        self.assertEqual(
            _progress_text("investigate_user", {"user": "alice@co"}),
            "\U0001F50E looking into that user's activity for alice@co",
        )

    def test_days_renders_last_n_d(self):
        self.assertEqual(
            _progress_text("run_audit", {"days": 7}),
            "\U0001F50E running the capacity audit (last 7d)",
        )

    def test_item_renders_for_item(self):
        self.assertEqual(
            _progress_text("describe_source", {"item": "X"}),
            "\U0001F50E checking what the data source contains for X",
        )

    def test_non_whitelisted_key_ignored(self):
        self.assertEqual(
            _progress_text("run_audit", {"format": "columnar"}),
            "\U0001F50E running the capacity audit",
        )


class TestHostileScopeValues(unittest.TestCase):
    def test_value_with_braces_dropped(self):
        self.assertEqual(
            _progress_text("investigate_user", {"user": "a{b}"}),
            "\U0001F50E looking into that user's activity",
        )

    def test_value_with_newline_dropped(self):
        self.assertEqual(
            _progress_text("investigate_user", {"user": "a\nb"}),
            "\U0001F50E looking into that user's activity",
        )

    def test_overlength_value_dropped(self):
        self.assertEqual(
            _progress_text("investigate_user", {"user": "x" * 100}),
            "\U0001F50E looking into that user's activity",
        )

    def test_hostile_value_never_format_breaks(self):
        for bad in ["a{b}", "a\nb", "x" * 100, "{", "}", "\n"]:
            with self.subTest(bad=bad):
                text = _progress_text("investigate_user", {"user": bad})
                self.assertNotIn("{", text)
                self.assertNotIn("}", text)
                self.assertNotIn("\n", text)

    def test_empty_or_whitespace_value_drops_hint_no_dangling_preposition(self):
        # An empty/whitespace value must not render a dangling "for " / "(top )".
        for empty in ["", "   ", "\t"]:
            with self.subTest(empty=repr(empty)):
                self.assertEqual(
                    _progress_text("investigate_user", {"user": empty}),
                    "\U0001F50E looking into that user's activity",
                )
        self.assertEqual(
            _progress_text("spike_events", {"topN": ""}),
            "\U0001F50E checking events with unusual spikes",
        )


if __name__ == "__main__":
    unittest.main()
