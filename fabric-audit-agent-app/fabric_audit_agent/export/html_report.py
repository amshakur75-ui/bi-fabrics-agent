"""html_report — self-contained, Newell-branded HTML report builder.

Reverse-engineered from the plugin's ``html-visualizer.ts`` (722 lines, read in
full). The plugin's ``renderHtmlVisualization`` classified columns, built an
ECharts option, rendered a single self-contained .html file and WROTE it to
``~/Downloads``. This app is hosted and multi-user, so the file-writing entry
point is dropped: ``build_html_report`` RETURNS the HTML text and the caller
(an export tool + download route) decides where it goes. Nothing touches a
user's local disk.

Everything else is ported exactly:
  * column classifier  — datetime+numeric -> line (<=5 series);
                          categorical+numeric -> vertical bar (<=20 uniques)
                          else horizontal bar; <=4 value columns; else table-only
  * Newell brand tokens — Blue #288FC2 / Navy #01405C / Body Gray #696158 +
                          the 7-colour accent palette, Arial throughout
  * layout             — navy header ("… INFORMATION DELIVERY · NEWELL BRANDS"),
                          4px Blue->Navy gradient bar, KPI meta cards, an
                          ECharts 5.5.1 CDN chart, a sticky-header data table
                          capped at 2,000 rows, timestamp footer
  * ExecutingUser normalization on every displayed cell
  * esc() on ALL interpolated values

``columns`` are the codebase's ``[{"name": str, "type": str}, ...]`` dicts (the
shape returned by the collectors and tool results); ``rows`` are lists of cell
values. Column objects with ``.name``/``.type`` attributes are also accepted.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from fabric_audit_agent.export.html_utils import esc

__all__ = ["build_html_report", "normalize_executing_user_display"]

# ── Newell brand constants (tightening.md 26n) ──────────────────────────────
NEWELL_BLUE = "#288FC2"
NEWELL_NAVY = "#01405C"
BODY_GRAY = "#696158"
PALETTE = [NEWELL_BLUE, NEWELL_NAVY, "#7D87C2", "#BCC883", "#EEB927", "#F89848", "#E34154"]
MAX_TABLE_ROWS = 2_000
ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"

# ── ExecutingUser normalization (mirrors constants.ts) ──────────────────────
EXECUTING_USER_COLUMN = "ExecutingUser"
NEWELL_EMAIL_DOMAIN = "@newellco.com"
_EXECUTING_USER_LOWER = EXECUTING_USER_COLUMN.lower()


def normalize_executing_user_display(raw: Any) -> str:
    """Port of format.ts ``normalizeExecutingUserDisplay``.

    Pure — returns a normalized string, never mutates the input.
      * ``None`` / empty            -> ``""`` (never synthesize a fake address)
      * already contains ``@``      -> returned unchanged (any domain)
      * a bare username             -> ``@newellco.com`` appended, casing kept

    The plugin's contract per the task: a value that already looks like an
    address is left as-is; a bare username gets the Newell domain. The function
    MUST exist and be applied on every displayed cell of the ExecutingUser
    column (defense-in-depth identity display rule).
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if s == "":
        return ""
    return s if "@" in s else f"{s}{NEWELL_EMAIL_DOMAIN}"


# ── Column shape helpers ────────────────────────────────────────────────────
def _col_name(col: Any) -> str:
    name = col.get("name") if isinstance(col, dict) else getattr(col, "name", None)
    return "" if name is None else str(name)


def _col_type(col: Any) -> str:
    ctype = col.get("type") if isinstance(col, dict) else getattr(col, "type", None)
    return "" if ctype is None else str(ctype)


def _is_datetime(col: Any) -> bool:
    return re.search(r"datetime", _col_type(col), re.IGNORECASE) is not None


def _is_numeric(col: Any) -> bool:
    return re.match(r"(int|long|real|double|decimal)\b", _col_type(col), re.IGNORECASE) is not None


def _index_of(columns: Sequence[Any], target: Any) -> int:
    """Identity-based index (mirrors TS ``columns.indexOf(col)`` reference match)."""
    for i, c in enumerate(columns):
        if c is target:
            return i
    return -1


def _unique_count(rows: Sequence[Sequence[Any]], col_idx: int) -> int:
    seen: set[str] = set()
    for row in rows:
        val = row[col_idx] if col_idx < len(row) else None
        seen.add("" if val is None else str(val))
    return len(seen)


# ── Classification ──────────────────────────────────────────────────────────
@dataclass
class _ColumnClassification:
    visual_type: str  # "line" | "bar-vertical" | "bar-horizontal" | "table-only"
    category_col: Optional[Any]
    value_columns: list


def _classify_columns(columns: Sequence[Any], rows: Sequence[Sequence[Any]]) -> _ColumnClassification:
    datetime_cols = [c for c in columns if _is_datetime(c)]
    numeric_cols = [c for c in columns if _is_numeric(c)]
    categorical_cols = [c for c in columns if not _is_datetime(c) and not _is_numeric(c)]

    if datetime_cols and numeric_cols:
        return _ColumnClassification("line", datetime_cols[0], list(numeric_cols[:5]))

    if categorical_cols and numeric_cols:
        cat_col = categorical_cols[0]
        cat_idx = _index_of(columns, cat_col)
        uniques = _unique_count(rows, cat_idx) if cat_idx >= 0 else 0
        v_type = "bar-horizontal" if uniques > 20 else "bar-vertical"
        return _ColumnClassification(v_type, cat_col, list(numeric_cols[:4]))

    return _ColumnClassification("table-only", None, [])


def _visual_type_label(t: str) -> str:
    return {
        "line": "Line Chart",
        "bar-vertical": "Bar Chart",
        "bar-horizontal": "Horizontal Bar",
        "table-only": "Data Table",
    }[t]


# ── ECharts option builder ──────────────────────────────────────────────────
def _to_number(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool):  # bool is an int subclass — reject like JS NaN handling
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        n = float(str(v))
    except (ValueError, TypeError):
        return None
    return None if n != n else n  # NaN guard


def _build_echarts_option(
    cls: _ColumnClassification,
    columns: Sequence[Any],
    rows: Sequence[Sequence[Any]],
    title: str,
) -> dict:
    base_option: dict = {
        "backgroundColor": "transparent",
        "color": PALETTE,
        "textStyle": {"fontFamily": "Arial, sans-serif", "color": BODY_GRAY},
        "tooltip": {
            "trigger": "axis",
            "backgroundColor": NEWELL_NAVY,
            "borderColor": NEWELL_BLUE,
            "borderWidth": 1,
            "textStyle": {"color": "#ffffff", "fontFamily": "Arial, sans-serif", "fontSize": 13},
            "axisPointer": {"lineStyle": {"color": NEWELL_BLUE, "width": 1, "type": "dashed"}},
        },
        "legend": {
            "bottom": 8,
            "textStyle": {"color": BODY_GRAY, "fontFamily": "Arial, sans-serif", "fontSize": 12},
            "icon": "roundRect",
        },
        "grid": {"left": 20, "right": 32, "top": 16, "bottom": 48, "containLabel": True},
    }

    if cls.visual_type == "table-only" or cls.category_col is None:
        return {
            **base_option,
            "title": {
                "text": "No chart — data table below",
                "textStyle": {"color": BODY_GRAY, "fontSize": 14, "fontWeight": "normal"},
                "left": "center",
                "top": "middle",
            },
        }

    cat_col = cls.category_col
    cat_idx = _index_of(columns, cat_col)

    max_chart_cats = 40 if cls.visual_type == "bar-horizontal" else 200
    chart_rows = list(rows[:max_chart_cats])
    categories = [
        "" if (cat_idx < 0 or cat_idx >= len(row) or row[cat_idx] is None) else str(row[cat_idx])
        for row in chart_rows
    ]

    series: list[dict] = []
    for col in cls.value_columns:
        col_idx = _index_of(columns, col)
        data = [
            _to_number(row[col_idx]) if (col_idx >= 0 and col_idx < len(row)) else None
            for row in chart_rows
        ]
        if cls.visual_type == "line":
            series.append({
                "name": _col_name(col),
                "type": "line",
                "data": data,
                "smooth": True,
                "symbol": "circle",
                "symbolSize": 4,
                "lineStyle": {"width": 2},
                "emphasis": {"focus": "series"},
            })
        elif cls.visual_type == "bar-horizontal":
            series.append({
                "name": _col_name(col),
                "type": "bar",
                "data": data,
                "barMaxWidth": 32,
                "emphasis": {"focus": "series"},
            })
        else:  # bar-vertical
            series.append({
                "name": _col_name(col),
                "type": "bar",
                "data": data,
                "barMaxWidth": 48,
                "emphasis": {"focus": "series"},
            })

    axis_base: dict = {
        "axisLine": {"lineStyle": {"color": NEWELL_BLUE, "width": 1}},
        "axisTick": {"lineStyle": {"color": NEWELL_BLUE}},
        "axisLabel": {"color": BODY_GRAY, "fontFamily": "Arial, sans-serif", "fontSize": 11},
        "splitLine": {"lineStyle": {"color": "#E8EDF0", "type": "dashed"}},
    }

    if cls.visual_type == "bar-horizontal":
        return {
            **base_option,
            "yAxis": {"type": "category", "data": categories, **axis_base, "inverse": True},
            "xAxis": {"type": "value", **axis_base},
            "series": series,
        }

    x_axis: dict = {
        "type": "time" if cls.visual_type == "line" else "category",
        **axis_base,
        "axisLabel": {**axis_base["axisLabel"], "rotate": 35 if len(categories) > 10 else 0},
    }
    # TS sets ``data: undefined`` for the "time" axis (dropped by JSON.stringify);
    # only the category axis carries the extracted category values.
    if cls.visual_type != "line":
        x_axis["data"] = categories

    return {
        **base_option,
        "xAxis": x_axis,
        "yAxis": {"type": "value", **axis_base},
        "series": series,
    }


# ── Timestamp ───────────────────────────────────────────────────────────────
def _timestamp() -> str:
    """Mirror TS ``new Date().toISOString().replace(/\\.\\d{3}Z$/, "Z")`` -> UTC, second precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── CSS (literal brand hex; braces kept literal, tokens substituted) ────────
_CSS_TEMPLATE = """
    /* Reset & base */
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html { font-size: 14px; }
    body {
      font-family: Arial, sans-serif;
      background: #F5F7FA;
      color: %%GRAY%%;
      min-height: 100vh;
    }

    /* Header */
    .header {
      background: %%NAVY%%;
      padding: 0 40px;
      height: 72px;
      display: flex;
      align-items: center;
      gap: 0;
    }
    .header-brand { display: flex; flex-direction: column; gap: 2px; padding-right: 28px; }
    .header-brand-title {
      color: #ffffff; font-size: 18px; font-weight: bold;
      letter-spacing: 0.12em; text-transform: uppercase;
    }
    .header-brand-sub {
      color: rgba(255,255,255,0.55); font-size: 10px;
      letter-spacing: 0.18em; text-transform: uppercase;
    }
    .header-divider { width: 1px; height: 36px; background: %%BLUE%%; margin: 0 28px; flex-shrink: 0; }
    .header-doc { display: flex; flex-direction: column; gap: 3px; }
    .header-doc-title { color: #ffffff; font-size: 15px; font-weight: bold; }
    .header-doc-meta { color: rgba(255,255,255,0.5); font-size: 11px; }

    /* Accent bar */
    .accent-bar { height: 4px; background: linear-gradient(90deg, %%BLUE%% 0%, %%NAVY%% 100%); }

    /* Page content */
    .content { max-width: 1440px; margin: 0 auto; padding: 36px 40px 48px; }

    /* KPI meta cards */
    .meta-bar { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 28px; }
    .meta-card {
      background: #ffffff; border-radius: 8px; padding: 14px 22px;
      border-left: 4px solid %%BLUE%%;
      box-shadow: 0 1px 4px rgba(1,64,92,0.10), 0 0 0 1px rgba(1,64,92,0.04);
      min-width: 130px;
    }
    .meta-label {
      font-size: 10px; text-transform: uppercase; letter-spacing: 0.10em;
      color: %%GRAY%%; margin-bottom: 6px; font-weight: bold;
    }
    .meta-value { font-size: 22px; font-weight: bold; color: %%NAVY%%; line-height: 1; }

    /* Cards */
    .card {
      background: #ffffff; border-radius: 8px;
      box-shadow: 0 1px 4px rgba(1,64,92,0.10), 0 0 0 1px rgba(1,64,92,0.04);
      margin-bottom: 24px; overflow: hidden;
    }
    .card-header {
      padding: 14px 24px; border-bottom: 1px solid #EBF0F3;
      display: flex; align-items: baseline; gap: 12px; background: #FAFCFD;
    }
    .card-title {
      font-size: 13px; font-weight: bold; color: %%NAVY%%;
      text-transform: uppercase; letter-spacing: 0.06em;
    }
    .card-subtitle { font-size: 11px; color: %%GRAY%%; }
    .card-body { padding: 24px; }

    /* Chart */
    #chart-container { width: 100%; height: 420px; }
    .no-chart-msg {
      display: flex; align-items: center; justify-content: center;
      height: 120px; color: %%GRAY%%; font-size: 13px; font-style: italic;
    }

    /* Truncation banner */
    .truncation-banner {
      background: #FFF8E6; border: 1px solid #EEB927; border-radius: 6px;
      padding: 10px 16px; font-size: 12px; color: #7A5C00; margin-bottom: 16px;
    }

    /* Data table */
    .table-wrap { overflow-x: auto; border-radius: 6px; border: 1px solid #E0E8ED; }
    table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
    thead th {
      background: %%NAVY%%; color: #ffffff; padding: 10px 14px; text-align: left;
      font-weight: bold; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.07em;
      white-space: nowrap; position: sticky; top: 0; z-index: 1;
      border-right: 1px solid rgba(255,255,255,0.08);
    }
    thead th:last-child { border-right: none; }
    tbody tr { transition: background 0.1s; }
    tbody tr:nth-child(even) { background: #F0F7FC; }
    tbody tr:hover { background: #D8EEF7; }
    tbody td {
      padding: 8px 14px; border-bottom: 1px solid #E8EDF0; border-right: 1px solid #F0F4F6;
      color: %%GRAY%%; white-space: nowrap; max-width: 360px; overflow: hidden; text-overflow: ellipsis;
    }
    tbody td:last-child { border-right: none; }
    tbody td:first-child { font-weight: 600; color: %%NAVY%%; }
    tbody tr:last-child td { border-bottom: none; }

    /* Footer */
    .footer {
      text-align: center; padding: 28px 0 8px; font-size: 11px;
      color: #AABBC4; letter-spacing: 0.04em;
    }
    .footer strong { color: %%BLUE%%; font-weight: normal; }
"""


def _render_css() -> str:
    return (
        _CSS_TEMPLATE
        .replace("%%BLUE%%", NEWELL_BLUE)
        .replace("%%NAVY%%", NEWELL_NAVY)
        .replace("%%GRAY%%", BODY_GRAY)
    )


# The inline bootstrap script. Kept as a plain template (no f-string) so the JS
# braces stay literal; the chart JSON is substituted with a sentinel.
_CHART_SCRIPT_TEMPLATE = """
  <script>
    (function () {
      var el = document.getElementById('chart-container');
      if (!el || typeof echarts === 'undefined') return;
      var chart = echarts.init(el, null, { renderer: 'canvas' });
      var option = %%CHART_JSON%%;
      chart.setOption(option);
      window.addEventListener('resize', function () { chart.resize(); });
    })();
  </script>"""


# ── Public builder ──────────────────────────────────────────────────────────
def build_html_report(
    columns: Sequence[Any],
    rows: Sequence[Sequence[Any]],
    title: str = "Query Results",
) -> str:
    """Build a self-contained, Newell-branded HTML report and RETURN it as text.

    Unlike the plugin's ``renderHtmlVisualization`` (which wrote the file to
    ``~/Downloads``), this performs no I/O — the caller decides where the string
    goes (download route, volume, in-chat artifact).
    """
    columns = list(columns)
    rows = list(rows)
    ts = _timestamp()

    cls = _classify_columns(columns, rows)
    type_label = _visual_type_label(cls.visual_type)
    row_count = len(rows)
    col_count = len(columns)
    show_chart = cls.visual_type != "table-only"

    chart_option = _build_echarts_option(cls, columns, rows, title)
    # Escape "<" so query-result strings containing "</script>" cannot break out
    # of the inline <script> block and inject markup into the generated file.
    chart_json = json.dumps(chart_option, separators=(",", ":")).replace("<", "\\u003c")

    table_rows = rows[:MAX_TABLE_ROWS]
    truncated = row_count > MAX_TABLE_ROWS

    header_cells = "".join(f"<th>{esc(_col_name(c))}</th>" for c in columns)

    body_row_html: list[str] = []
    for row in table_rows:
        cells: list[str] = []
        for ci, col in enumerate(columns):
            raw = row[ci] if ci < len(row) else None
            if _col_name(col).lower() == _EXECUTING_USER_LOWER and raw is not None:
                display = normalize_executing_user_display(raw)
            elif raw is None:
                display = ""
            else:
                display = str(raw)
            cells.append(f"<td>{esc(display)}</td>")
        body_row_html.append("<tr>" + "".join(cells) + "</tr>")
    body_rows = "\n            ".join(body_row_html)

    if truncated:
        truncation_banner = (
            '<div class="truncation-banner">\n'
            f"         Showing first {MAX_TABLE_ROWS:,} of {row_count:,} rows.\n"
            "         Export to Excel for the full dataset.\n"
            "       </div>"
        )
    else:
        truncation_banner = ""

    css = _render_css()
    header_meta = ts.replace("T", " at ").replace("Z", " UTC")
    script_block = (
        _CHART_SCRIPT_TEMPLATE.replace("%%CHART_JSON%%", chart_json) if show_chart else ""
    )
    echarts_tag = f'<script src="{ECHARTS_CDN}"></script>' if show_chart else ""

    chart_card = (
        (
            '\n    <!-- Chart card -->\n'
            '    <div class="card">\n'
            '      <div class="card-header">\n'
            '        <span class="card-title">Visualization</span>\n'
            f'        <span class="card-subtitle">{esc(type_label)} &mdash; auto-selected from column shape</span>\n'
            "      </div>\n"
            '      <div class="card-body">\n'
            '        <div id="chart-container"></div>\n'
            "      </div>\n"
            "    </div>"
        )
        if show_chart
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>KQL Analytics — {esc(title)}</title>
  {echarts_tag}
  <style>{css}  </style>
</head>
<body>

  <!-- Header -->
  <header class="header">
    <div class="header-brand">
      <div class="header-brand-title">KQL Analytics</div>
      <div class="header-brand-sub">Information Delivery &middot; Newell Brands</div>
    </div>
    <div class="header-divider"></div>
    <div class="header-doc">
      <div class="header-doc-title">{esc(title)}</div>
      <div class="header-doc-meta">Generated {esc(header_meta)}</div>
    </div>
  </header>
  <div class="accent-bar"></div>

  <!-- Main content -->
  <main class="content">

    <!-- KPI meta bar -->
    <div class="meta-bar">
      <div class="meta-card">
        <div class="meta-label">Rows</div>
        <div class="meta-value">{row_count:,}</div>
      </div>
      <div class="meta-card">
        <div class="meta-label">Columns</div>
        <div class="meta-value">{col_count}</div>
      </div>
      <div class="meta-card">
        <div class="meta-label">Chart Type</div>
        <div class="meta-value" style="font-size:14px;padding-top:4px;">{esc(type_label)}</div>
      </div>
    </div>
{chart_card}

    <!-- Data table card -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">Data</span>
        <span class="card-subtitle">{row_count:,} rows &times; {col_count} columns</span>
      </div>
      <div class="card-body">
        {truncation_banner}
        <div class="table-wrap">
          <table>
            <thead>
              <tr>{header_cells}</tr>
            </thead>
            <tbody>
            {body_rows}
            </tbody>
          </table>
        </div>
      </div>
    </div>

  </main>

  <!-- Footer -->
  <footer class="footer">
    Generated by <strong>Fabric Audit Agent</strong> &middot; Information Delivery &middot; Newell Brands &middot; {esc(ts)}
  </footer>
{script_block}
</body>
</html>"""
