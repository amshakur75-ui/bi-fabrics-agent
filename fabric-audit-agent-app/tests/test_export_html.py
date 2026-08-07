"""Tests for the HTML export builder (Phase 5.1 / 5.2).

Covers: the 5-char HTML escape, escaping applied through the report, the chart
auto-selection (line vs vertical bar vs horizontal bar vs table-only), presence
of the Newell brand hex tokens, ExecutingUser normalization on displayed cells,
and the 2,000-row table cap.
"""
import json

from fabric_audit_agent.export.html_utils import esc, file_timestamp
from fabric_audit_agent.export.html_report import (
    build_html_report,
    normalize_executing_user_display,
    NEWELL_BLUE,
    NEWELL_NAVY,
    BODY_GRAY,
    PALETTE,
    MAX_TABLE_ROWS,
)


# ── esc() ────────────────────────────────────────────────────────────────────
def test_esc_all_five_characters():
    assert esc("<>&\"'") == "&lt;&gt;&amp;&quot;&#39;"


def test_esc_ampersand_first_no_double_escape():
    # "&lt;" must not become "&amp;lt;" — & is replaced before < / >.
    assert esc("a<b") == "a&lt;b"
    assert esc("x & y") == "x &amp; y"


def test_esc_coerces_none_and_numbers():
    assert esc(None) == ""
    assert esc(42) == "42"


def test_file_timestamp_shape():
    ts = file_timestamp()
    # YYYY-MM-DDTHH-MM-SS — no ':' or '.' (filesystem-safe)
    assert len(ts) == 19 and ":" not in ts and "." not in ts and ts[10] == "T"


# ── normalize_executing_user_display ────────────────────────────────────────
def test_normalize_executing_user():
    assert normalize_executing_user_display("jsmith") == "jsmith@newellco.com"
    assert normalize_executing_user_display("a@b.com") == "a@b.com"
    assert normalize_executing_user_display(None) == ""
    assert normalize_executing_user_display("   ") == ""


# ── Brand tokens ─────────────────────────────────────────────────────────────
def test_report_contains_brand_hex_tokens():
    cols = [{"name": "Category", "type": "string"}, {"name": "Value", "type": "long"}]
    rows = [["A", 1], ["B", 2]]
    html = build_html_report(cols, rows, title="Brand Check")
    for token in (NEWELL_BLUE, NEWELL_NAVY, BODY_GRAY):
        assert token in html
    # header brand line
    assert "INFORMATION DELIVERY" in html.upper()
    assert "NEWELL BRANDS" in html.upper()


def test_report_palette_present_in_chart_json():
    cols = [{"name": "Category", "type": "string"}, {"name": "Value", "type": "long"}]
    rows = [["A", 1], ["B", 2]]
    html = build_html_report(cols, rows)
    # every accent-palette colour is embedded in the ECharts option
    for colour in PALETTE:
        assert colour in html


# ── Chart auto-selection ─────────────────────────────────────────────────────
def _chart_json_from_html(html: str) -> dict:
    marker = "var option = "
    start = html.index(marker) + len(marker)
    end = html.index(";\n", start)
    raw = html[start:end].replace("\\u003c", "<")
    return json.loads(raw)


def test_classifier_picks_line_for_datetime_plus_numeric():
    cols = [{"name": "TimeGenerated", "type": "datetime"}, {"name": "CpuTimeMs", "type": "long"}]
    rows = [["2026-08-01T00:00:00Z", 10], ["2026-08-01T01:00:00Z", 20]]
    html = build_html_report(cols, rows)
    assert "Line Chart" in html
    assert f'<script src="' in html  # ECharts CDN tag present -> chart rendered
    opt = _chart_json_from_html(html)
    assert opt["series"][0]["type"] == "line"
    assert opt["xAxis"]["type"] == "time"


def test_classifier_picks_vertical_bar_for_small_categorical():
    cols = [{"name": "Workspace", "type": "string"}, {"name": "Count", "type": "int"}]
    rows = [[f"WS{i}", i] for i in range(5)]  # 5 unique categories (<=20)
    html = build_html_report(cols, rows)
    assert "Bar Chart" in html
    opt = _chart_json_from_html(html)
    assert opt["series"][0]["type"] == "bar"
    assert opt["xAxis"]["type"] == "category"  # vertical bar keeps category on x


def test_classifier_picks_horizontal_bar_for_large_categorical():
    cols = [{"name": "User", "type": "string"}, {"name": "Count", "type": "long"}]
    rows = [[f"user{i}", i] for i in range(25)]  # 25 unique categories (>20)
    html = build_html_report(cols, rows)
    assert "Horizontal Bar" in html
    opt = _chart_json_from_html(html)
    # horizontal bar puts the category on the y-axis
    assert opt["yAxis"]["type"] == "category"


def test_classifier_falls_back_to_table_only():
    cols = [{"name": "Name", "type": "string"}, {"name": "Label", "type": "string"}]
    rows = [["a", "x"], ["b", "y"]]
    html = build_html_report(cols, rows)
    assert "Data Table" in html
    # No chart -> no ECharts CDN script tag, no chart <div>, no bootstrap script.
    # (The "#chart-container" CSS selector is always present and is harmless.)
    assert "echarts@5.5.1" not in html
    assert 'id="chart-container"' not in html
    assert "var option = " not in html


# ── Escaping through the report ──────────────────────────────────────────────
def test_cell_and_header_values_are_escaped():
    cols = [{"name": "<danger>", "type": "string"}, {"name": "V", "type": "long"}]
    rows = [["</script><script>alert(1)</script>", 1]]
    html = build_html_report(cols, rows)
    # raw injection must not survive
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;danger&gt;" in html


def test_executing_user_column_normalized_in_table():
    cols = [{"name": "ExecutingUser", "type": "string"}, {"name": "N", "type": "long"}]
    rows = [["jdoe", 3]]
    html = build_html_report(cols, rows)
    assert "jdoe@newellco.com" in html


# ── Row cap ──────────────────────────────────────────────────────────────────
def test_table_capped_and_truncation_banner():
    cols = [{"name": "Name", "type": "string"}, {"name": "Other", "type": "string"}]
    rows = [[f"r{i}", "x"] for i in range(MAX_TABLE_ROWS + 50)]
    html = build_html_report(cols, rows)
    # rendered <tr> count == header row + capped body rows
    body_tr = html.count("<tr>")
    assert body_tr == MAX_TABLE_ROWS + 1  # +1 header row
    assert "truncation-banner" in html
    assert f"{MAX_TABLE_ROWS:,}" in html


def test_returns_str_and_is_self_contained():
    html = build_html_report([{"name": "A", "type": "long"}], [[1]])
    assert isinstance(html, str)
    assert html.startswith("<!DOCTYPE html>")
