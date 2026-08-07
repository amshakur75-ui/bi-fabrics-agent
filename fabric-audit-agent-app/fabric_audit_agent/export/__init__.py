"""fabric_audit_agent.export — server-generated downloadable report artifacts.

Reverse-engineered from the KQL plugin's desktop visualizers (html-visualizer.ts,
visualizer.ts, html-utils.ts). The plugin wrote local .html/.xlsx files under the
user's ~/Downloads on a single-user desktop. This app is a hosted, multi-user
Databricks app, so the SAME capability becomes a *server-generated artifact*:
the builders RETURN content (an HTML string / xlsx bytes); nothing touches a
user's local disk. The caller (an export tool + download route) decides where
the returned content goes.

Public surface:
  - esc, file_timestamp            (html_utils)
  - build_html_report              (html_report) -> str
  - build_xlsx_report              (xlsx_report)  -> bytes
"""
from fabric_audit_agent.export.html_utils import esc, file_timestamp
from fabric_audit_agent.export.html_report import build_html_report
from fabric_audit_agent.export.xlsx_report import build_xlsx_report

__all__ = [
    "esc",
    "file_timestamp",
    "build_html_report",
    "build_xlsx_report",
]
