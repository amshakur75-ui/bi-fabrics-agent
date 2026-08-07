"""Tests for query.kql_format.format_kql — the pipe-per-line KQL indentation formatter ported
from the kql-mcp-server-v5 plugin's format.ts (MIT). Display-only: never changes semantics."""
from fabric_audit_agent.query.kql_format import format_kql


def test_none_and_empty_are_safe():
    assert format_kql(None) is None
    assert format_kql("") == ""
    assert format_kql("   ") == "   "


def test_non_string_input_returned_unchanged():
    assert format_kql(123) == 123
    assert format_kql(["not", "a", "string"]) == ["not", "a", "string"]


def test_multi_stage_query_gets_one_pipe_per_line():
    query = 'CapacityEvents | where TimeGenerated > ago(1d) | summarize count() by User | order by count_ desc'
    formatted = format_kql(query)
    lines = formatted.split("\n")
    assert lines[0] == "CapacityEvents"
    pipe_lines = [l for l in lines if l.strip().startswith("|")]
    assert len(pipe_lines) == 3
    assert pipe_lines[0].strip() == "| where TimeGenerated > ago(1d)"
    assert pipe_lines[1].strip() == "| summarize count() by User"
    assert pipe_lines[2].strip() == "| order by count_ desc"
    # Every pipe line is indented under the source table.
    assert all(l.startswith("  |") for l in pipe_lines)


def test_string_literal_containing_pipe_is_untouched():
    query = 'CapacityEvents | where EventText contains "a|b" | take 10'
    formatted = format_kql(query)
    assert '"a|b"' in formatted
    # Only the two real pipe operators were split out, not the one inside the string.
    pipe_lines = [l for l in formatted.split("\n") if l.strip().startswith("|")]
    assert len(pipe_lines) == 2
    assert 'contains "a|b"' in pipe_lines[0]


def test_verbatim_string_literal_with_pipe_is_untouched():
    query = "CapacityEvents | where EventText contains @\"x|y\" | take 5"
    formatted = format_kql(query)
    assert '@"x|y"' in formatted
    pipe_lines = [l for l in formatted.split("\n") if l.strip().startswith("|")]
    assert len(pipe_lines) == 2


def test_let_statements_get_blank_line_before_first_pipe_stage():
    query = "let x = 1;\nlet y = 2;\nCapacityEvents | where A == x | where B == y"
    formatted = format_kql(query)
    lines = formatted.split("\n")
    assert "let x = 1;" in lines
    assert "let y = 2;" in lines


def test_already_multiline_query_preserves_pipe_lines():
    query = "CapacityEvents\n| where TimeGenerated > ago(1d)\n| take 10"
    formatted = format_kql(query)
    pipe_lines = [l for l in formatted.split("\n") if l.strip().startswith("|")]
    assert len(pipe_lines) == 2


def test_comment_lines_pass_through():
    query = "// a comment\nCapacityEvents | take 1"
    formatted = format_kql(query)
    assert "// a comment" in formatted.split("\n")


def test_unterminated_string_literal_falls_back_to_original():
    # A stray unescaped quote makes the line unparseable as a string boundary — the formatter
    # must not guess and mangle it; it returns the input unchanged.
    query = 'CapacityEvents | where EventText contains "unterminated | take 5'
    formatted = format_kql(query)
    assert formatted == query


def test_single_pipe_stage_query():
    query = "CapacityEvents | take 5"
    formatted = format_kql(query)
    assert formatted == "CapacityEvents\n  | take 5"
