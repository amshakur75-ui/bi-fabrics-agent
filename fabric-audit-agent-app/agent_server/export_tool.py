"""export_html_report / export_xlsx_report as DIRECT tools for the agent (plan 5.4).

The plugin wrote Newell-branded .html / .xlsx files to a desktop ~/Downloads. This app is a
hosted, multi-user Databricks app, so the same capability becomes a SERVER-generated artifact:
the tool writes the report to a server-side export directory and returns a download id/path +
a summary. Nothing touches a user's local disk.

Follows agent_server/chart_tool.py's pattern exactly: a pure validate/coerce step
(``coerce_columns_rows``) + a thin handler, tolerant of the shapes a model naturally emits, and
dual registration via ``export_tool_and_dispatch()``.

REUSE, NEVER RE-EXECUTE (26p): the input columns+rows come from a PRIOR tool result already in
the conversation — the tool never re-runs a query. That rule is stated in the tool descriptions
so it travels with the schema.

The actual report builders live in ``fabric_audit_agent.export`` and are imported LAZILY inside
the handler (openpyxl is a heavy optional dep) so importing this module is always cheap and never
hard-requires the package.
"""
import os
import re
import tempfile
import uuid

_MAX_EXPORT_ROWS = 100_000  # hard ceiling so a runaway rows payload can't exhaust disk


def _export_dir():
    """Server-side directory the app can serve exports from. Overridable via env; created on
    first use. Not a user's local disk — a hosted, app-owned scratch/volume path."""
    d = os.environ.get("FABRIC_EXPORT_DIR") or os.path.join(tempfile.gettempdir(), "fabric-exports")
    os.makedirs(d, exist_ok=True)
    return d


def _coerce_columns(columns):
    """Normalize columns to the builders' ``[{"name","type"}, ...]`` shape. Tolerates a plain
    list of column-name strings and dicts missing a type. Returns ``(columns, None)`` or
    ``(None, error_message)``."""
    if not isinstance(columns, list) or len(columns) == 0:
        return None, "columns must be a non-empty list of column definitions"
    out = []
    for i, c in enumerate(columns):
        if isinstance(c, str):
            out.append({"name": c, "type": ""})
        elif isinstance(c, dict):
            name = c.get("name")
            if not name or not str(name).strip():
                return None, f"columns[{i}] is missing a name"
            out.append({"name": str(name), "type": str(c.get("type") or "")})
        else:
            return None, f"columns[{i}] must be a string or a {{name, type}} object"
    return out, None


def _coerce_rows(rows, columns):
    """Normalize rows to a list of positional cell lists aligned to ``columns``. Tolerates
    list-of-lists (used as-is) and list-of-dicts (keyed by column name -> positional). Returns
    ``(rows, None)`` or ``(None, error_message)``."""
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        return None, "rows must be a list of row values"
    if len(rows) > _MAX_EXPORT_ROWS:
        return None, f"rows exceeds the {_MAX_EXPORT_ROWS:,}-row export ceiling"
    names = [c["name"] for c in columns]
    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append([r.get(n) for n in names])
        elif isinstance(r, (list, tuple)):
            out.append(list(r))
        else:
            # a scalar row for a single-column export
            out.append([r])
    return out, None


def coerce_columns_rows(inp):
    """Validate + coerce a ``{columns, rows, title}`` payload. Returns
    ``(columns, rows, title, None)`` on success or ``(None, None, None, error_message)``."""
    inp = inp or {}
    columns, err = _coerce_columns(inp.get("columns"))
    if err:
        return None, None, None, err
    rows, err = _coerce_rows(inp.get("rows"), columns)
    if err:
        return None, None, None, err
    title = inp.get("title")
    title = str(title).strip() if title and str(title).strip() else "Query Results"
    return columns, rows, title, None


def _safe_filename(title, ext):
    """A filesystem-safe, collision-proof filename derived from the title + a uuid."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(title)).strip("-").lower() or "report"
    return f"{slug[:60]}-{uuid.uuid4().hex[:8]}.{ext}"


def _write_export(content, filename):
    """Write bytes/str to the export dir; return ``(download_id, absolute_path)``."""
    path = os.path.join(_export_dir(), filename)
    mode = "wb" if isinstance(content, (bytes, bytearray)) else "w"
    kwargs = {} if isinstance(content, (bytes, bytearray)) else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as f:
        f.write(content)
    return filename, path


def export_html_report_result(inp):
    """Pure-ish handler for export_html_report: coerce -> build HTML -> write -> return summary.
    Reuses columns+rows from a prior tool result; never re-executes a query."""
    columns, rows, title, err = coerce_columns_rows(inp)
    if err:
        return {"error": err}
    try:
        from fabric_audit_agent.export import build_html_report
    except Exception as exc:  # pragma: no cover - packaging/import failure
        return {"error": f"HTML report builder unavailable: {exc}"}
    html = build_html_report(columns, rows, title)
    download_id, path = _write_export(html, _safe_filename(title, "html"))
    return {
        "format": "html",
        "downloadId": download_id,
        "downloadPath": path,
        "filename": download_id,
        "title": title,
        "rowCount": len(rows),
        "columnCount": len(columns),
        "byteCount": len(html.encode("utf-8")),
        "summary": (
            f"Built a Newell-branded HTML report '{title}' ({len(rows):,} rows x {len(columns)} "
            f"columns). Download id: {download_id}."
        ),
    }


def export_xlsx_report_result(inp):
    """Pure-ish handler for export_xlsx_report: coerce -> build xlsx bytes -> write -> summary.
    Reuses columns+rows from a prior tool result; never re-executes a query."""
    columns, rows, title, err = coerce_columns_rows(inp)
    if err:
        return {"error": err}
    try:
        from fabric_audit_agent.export import build_xlsx_report
    except ImportError as exc:  # openpyxl (or the package) missing
        return {"error": f"Excel report builder unavailable: {exc}"}
    try:
        data = build_xlsx_report(columns, rows, title)
    except ImportError as exc:  # openpyxl resolved lazily inside the builder
        return {"error": f"Excel report builder unavailable: {exc}"}
    download_id, path = _write_export(bytes(data), _safe_filename(title, "xlsx"))
    return {
        "format": "xlsx",
        "downloadId": download_id,
        "downloadPath": path,
        "filename": download_id,
        "title": title,
        "rowCount": len(rows),
        "columnCount": len(columns),
        "byteCount": len(data),
        "summary": (
            f"Built a typed Excel report '{title}' ({len(rows):,} rows x {len(columns)} columns, "
            f"auto-selected chart). Download id: {download_id}."
        ),
    }


_COLUMNS_SCHEMA = {
    "type": "array",
    "description": (
        "Column definitions from the PRIOR tool result — each {name, type} (type is the KQL "
        "column type, e.g. 'datetime'/'long'/'string', and drives chart auto-selection). A plain "
        "list of column-name strings is also accepted."
    ),
    "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Column name."},
            "type": {"type": "string", "description": "Column data type (e.g. datetime, long, real, string)."},
        },
        "required": ["name"],
    },
}
_ROWS_SCHEMA = {
    "type": "array",
    "description": (
        "Row data from the PRIOR tool result — a list of rows, each row a list of cell values "
        "aligned to columns (a list of {columnName: value} objects is also accepted)."
    ),
    "items": {"type": "array"},
}
_TITLE_SCHEMA = {"type": "string", "description": "Report title (shown in the header)."}

_REUSE_RULE = (
    " IMPORTANT: pass the columns and rows from a PRIOR tool result already in this conversation — "
    "do NOT re-run the query. Read-only; writes a server-side downloadable artifact only."
)

_HTML_TOOL = {
    "name": "export_html_report",
    "description": (
        "Generate a self-contained, Newell-branded HTML report (KPI cards, an auto-selected "
        "ECharts chart, and a sticky-header data table) from query results, and return a download "
        "id/path. Use when the user asks for an HTML/branded/visual report of results they've "
        "already seen." + _REUSE_RULE
    ),
    "input_schema": {
        "type": "object",
        "properties": {"columns": _COLUMNS_SCHEMA, "rows": _ROWS_SCHEMA, "title": _TITLE_SCHEMA},
        "required": ["columns", "rows"],
    },
}

_XLSX_TOOL = {
    "name": "export_xlsx_report",
    "description": (
        "Generate a typed Excel (.xlsx) report — real datetimes/numbers, auto-filter + frozen "
        "header, and a native auto-selected chart — from query results, and return a download "
        "id/path. Use when the user asks to export results to Excel." + _REUSE_RULE
    ),
    "input_schema": {
        "type": "object",
        "properties": {"columns": _COLUMNS_SCHEMA, "rows": _ROWS_SCHEMA, "title": _TITLE_SCHEMA},
        "required": ["columns", "rows"],
    },
}


def export_tool_and_dispatch():
    """Return ``([tools], {name: async handler})`` for the direct export tools."""
    async def html_handler(inp):
        return export_html_report_result(inp)

    async def xlsx_handler(inp):
        return export_xlsx_report_result(inp)

    return ([_HTML_TOOL, _XLSX_TOOL],
            {"export_html_report": html_handler, "export_xlsx_report": xlsx_handler})
