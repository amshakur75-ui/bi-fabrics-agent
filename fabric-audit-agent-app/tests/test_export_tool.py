"""Plan 5.4 — the direct export tools (export_html_report / export_xlsx_report).

Mirrors test_chart_tool.py's shape: pure validate/coerce + a thin handler, dual registration,
tolerant coercion of the columns+rows shapes a model emits, and a server-side artifact written to
a temp export dir (never a user's local disk). The tools REUSE a prior tool result's data — they
never re-execute a query."""
import asyncio
import os

import pytest

from agent_server.export_tool import (
    coerce_columns_rows,
    export_html_report_result,
    export_xlsx_report_result,
    export_tool_and_dispatch,
)


@pytest.fixture(autouse=True)
def _tmp_export_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FABRIC_EXPORT_DIR", str(tmp_path / "exports"))


_COLUMNS = [{"name": "ExecutingUser", "type": "string"}, {"name": "QueryCount", "type": "long"}]
_ROWS = [["jdoe", 12], ["asmith", 7]]


# ── coercion ────────────────────────────────────────────────────────────────────────
def test_coerce_accepts_dict_columns_and_list_rows():
    cols, rows, title, err = coerce_columns_rows({"columns": _COLUMNS, "rows": _ROWS, "title": "T"})
    assert err is None and title == "T"
    assert cols[0]["name"] == "ExecutingUser" and rows == [["jdoe", 12], ["asmith", 7]]


def test_coerce_accepts_string_columns_and_dict_rows():
    cols, rows, title, err = coerce_columns_rows(
        {"columns": ["ExecutingUser", "QueryCount"],
         "rows": [{"ExecutingUser": "jdoe", "QueryCount": 12}]})
    assert err is None
    assert cols == [{"name": "ExecutingUser", "type": ""}, {"name": "QueryCount", "type": ""}]
    assert rows == [["jdoe", 12]]                          # dict row -> positional, column-aligned
    assert title == "Query Results"                        # default title


def test_coerce_rejects_empty_columns():
    _, _, _, err = coerce_columns_rows({"columns": [], "rows": []})
    assert err


# ── HTML export ───────────────────────────────────────────────────────────────────
def test_html_export_writes_file_and_returns_summary():
    out = export_html_report_result({"columns": _COLUMNS, "rows": _ROWS, "title": "Usage"})
    assert out["format"] == "html"
    assert out["rowCount"] == 2 and out["columnCount"] == 2
    assert os.path.isfile(out["downloadPath"])
    html = open(out["downloadPath"], encoding="utf-8").read()
    assert "Newell" in html and "jdoe@newellco.com" in html    # identity normalized in the export


def test_html_export_rejects_bad_input():
    assert "error" in export_html_report_result({"columns": [], "rows": []})


# ── XLSX export ───────────────────────────────────────────────────────────────────
def test_xlsx_export_writes_workbook():
    pytest.importorskip("openpyxl")
    out = export_xlsx_report_result({"columns": _COLUMNS, "rows": _ROWS, "title": "Usage"})
    assert out["format"] == "xlsx" and out["byteCount"] > 0
    assert os.path.isfile(out["downloadPath"])
    with open(out["downloadPath"], "rb") as fh:
        assert fh.read(2) == b"PK"                              # a real .xlsx zip


# ── dual registration ───────────────────────────────────────────────────────────────
def test_registration_exposes_both_tools_with_reuse_rule():
    tools, dispatch = export_tool_and_dispatch()
    names = {t["name"] for t in tools}
    assert names == {"export_html_report", "export_xlsx_report"}
    assert "export_html_report" in dispatch and "export_xlsx_report" in dispatch
    for t in tools:
        assert t["input_schema"]["required"] == ["columns", "rows"]
        # 26p: the reuse-don't-re-execute rule travels with the tool description.
        assert "do NOT re-run" in t["description"] or "PRIOR tool result" in t["description"]


def test_dispatch_handlers_are_async():
    _, dispatch = export_tool_and_dispatch()
    out = asyncio.run(dispatch["export_html_report"]({"columns": _COLUMNS, "rows": _ROWS}))
    assert out["format"] == "html"
