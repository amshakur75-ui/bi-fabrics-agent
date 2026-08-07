"""Shared, pure loop hooks for the ReAct tool-loop TWINS (tightening 24b / plan 3.10).

There are THREE hooks, applied identically in BOTH tool-loop twins — the async
``agent.py::_run_tool_loop`` and the sync ``loop.py::run_tool_loop``. They live here, once,
so the two loops cannot drift: each twin calls these functions at the two documented seams
(pre-tool-execution, post-tool-execution — see tasks/BLAST-RADIUS-CORE.md §4). A test asserts
both twins produce identical hook behaviour.

  (c) PRE-tool-execution — PBI-usage redirect (``pretool_pbi_usage_redirect``): the CRITICAL
      hook. If a general KQL/DAX generation/execution tool was called with a query that is a
      Power-BI field/measure usage question hand-authored as a raw ``EventText`` filter, the
      generated query is DISCARDED and the model is routed to the authoritative field/usage
      resolver instead (26l). It fires even if the system-prompt rule was missed.

  (a) POST-tool-execution — auto-analysis nudge (``post_tool_result``): after a query-execution
      tool returns rows, attach a nudge telling the model to ANALYSE the rows and answer the
      question, rather than dump the raw table (the plugin's auto ``kql_analyze`` trigger).

  (b) POST-tool-execution — identity-display normalization (``post_tool_result``): after any
      execution whose rows carry an ``ExecutingUser`` column, normalize that column at the DATA
      layer (structured rows) so a bare UPN becomes a full ``@newellco.com`` address — the
      three-point normalizeExecutingUserDisplay discipline (24e), applied here at the structured
      layer that both the display and the export downstream then inherit.

Pure + deterministic: no I/O, no import of either loop, never raises. Hook (c) prefers the ported
``kql_audit_rules.check_correct007`` (consuming the audit engine when the package is importable),
with a self-contained fallback so the decoupled agent app still enforces the redirect if the
package isn't present in the deployed runtime.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# General KQL/DAX generation/execution tools whose output hook (c) inspects for a
# hand-authored Power-BI usage filter it should redirect away from.
QUERY_GENERATION_TOOLS = frozenset({"run_kql", "run_dax"})

# Query-execution tools whose row results earn the auto-analysis nudge (hook a).
QUERY_EXECUTION_TOOLS = frozenset({"run_kql", "run_sql", "run_dax"})

EXECUTING_USER_COLUMN = "ExecutingUser"
_NEWELL_EMAIL_DOMAIN = "@newellco.com"

_ANALYSIS_NUDGE = (
    "Analyze these results and answer the user's question directly — surface what the rows MEAN "
    "(the who/what/why), do not just echo the raw table. If the user explicitly asked only for the "
    "raw rows or the query itself, skip the analysis."
)

_REDIRECT_MESSAGE = (
    "This looks like a Power BI field/measure usage question answered with a HAND-AUTHORED "
    "EventText filter — that produces wrong or overbroad results and is not allowed. The generated "
    "query was discarded and NOT executed. Do NOT write, edit, or verify an EventText filter "
    "yourself. Instead call the field resolver / field-usage builder (resolve_field then "
    "field_usage_query, or workspace_usage_query for adoption questions) to get an authoritative, "
    "provenance-tracked query built from the canonical DAX/MDX patterns, then run THAT."
)


# ── Hook (c): PBI-usage redirect detection ──────────────────────────────────────────
try:  # Prefer the ported audit engine (consumes kql_audit_rules when importable).
    from fabric_audit_agent.query.kql_audit_rules import check_correct007 as _check_correct007
except Exception:  # pragma: no cover - decoupled-deploy fallback path
    _check_correct007 = None

# Self-contained fallback mirroring CORRECT007's essential signal: a PowerBIDatasetsWorkspace
# query filtering EventText WITHOUT an authoritative bracketed DAX/MDX pattern. Used only when
# the ported check is not importable in the deployed runtime.
_FIRST_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_EVENTTEXT_FILTER_RE = re.compile(r"\bEventText\b\s*(?:contains|has(?:_any)?|==|=~)", re.IGNORECASE)
_BRACKETED_CONTAINS_RE = re.compile(
    r"""\bEventText\b\s*contains\s*(["'])((?:\\.|(?!\1).)*)\1""", re.IGNORECASE
)


def _fallback_improvised_pbi_filter(query: str) -> bool:
    stripped = re.sub(r"//[^\n]*", "", query)
    m = _FIRST_TOKEN_RE.match(stripped.strip())
    if not m or m.group(0) != "PowerBIDatasetsWorkspace":
        return False
    for line in query.splitlines():
        if not _EVENTTEXT_FILTER_RE.search(line):
            continue
        bracket = _BRACKETED_CONTAINS_RE.search(line)
        # Authoritative iff a bracketed ('Table'[Field] / [Measures].[Field]) contains operand.
        if bracket is not None and ("[" in (bracket.group(2) or "") or "]" in (bracket.group(2) or "")):
            continue
        return True
    return False


def _query_text(inp: Any) -> str:
    if not isinstance(inp, dict):
        return ""
    for key in ("kql", "dax", "query"):
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def pretool_pbi_usage_redirect(name: str, inp: Any) -> Optional[Dict]:
    """Hook (c). Return a redirect result dict if this tool call should be intercepted and
    routed to the field/usage resolver instead of executed; otherwise ``None``. Pure."""
    if name not in QUERY_GENERATION_TOOLS:
        return None
    query = _query_text(inp)
    if not query:
        return None
    try:
        if _check_correct007 is not None:
            improvised = bool(_check_correct007(query))
        else:  # pragma: no cover - fallback path
            improvised = _fallback_improvised_pbi_filter(query)
    except Exception:  # pragma: no cover - never let a hook raise
        improvised = False
    if not improvised:
        return None
    return {
        "redirected": True,
        "redirectStage": "pbi-usage",
        "kql": "",  # mirrors the plugin's REDIRECT_PBI_USAGE empty-query contract (26l)
        "message": _REDIRECT_MESSAGE,
    }


# ── Hook (b): ExecutingUser identity-display normalization ───────────────────────────
def normalize_executing_user_display(raw: Any) -> str:
    """Self-contained port of format.ts normalizeExecutingUserDisplay (kept in sync with
    fabric_audit_agent.export). Bare username -> ``@newellco.com`` appended; already an address
    -> unchanged; None/empty -> ``""``. Never synthesizes a fake address."""
    if raw is None:
        return ""
    s = str(raw).strip()
    if s == "":
        return ""
    return s if "@" in s else f"{s}{_NEWELL_EMAIL_DOMAIN}"


def _normalize_executing_user_rows(result: Dict) -> bool:
    """Normalize the ExecutingUser cell of every structured row dict, in place. Returns True if
    any cell was touched. Defensive: only acts on a ``rows`` list of dicts carrying the column."""
    rows = result.get("rows")
    if not isinstance(rows, list):
        return False
    touched = False
    for row in rows:
        if isinstance(row, dict) and EXECUTING_USER_COLUMN in row:
            row[EXECUTING_USER_COLUMN] = normalize_executing_user_display(row[EXECUTING_USER_COLUMN])
            touched = True
    return touched


def post_tool_result(name: str, inp: Any, result: Any) -> Any:
    """Hooks (a) + (b), applied post-execution. Returns the (possibly annotated) result. Pure
    w.r.t. inputs other than the in-place normalization of the result's own rows; never raises."""
    if not isinstance(result, dict):
        return result
    # Hook (b): identity-display normalization at the structured-row layer (any tool).
    _normalize_executing_user_rows(result)
    # Hook (a): auto-analysis nudge after a query-execution tool that actually returned rows.
    if name in QUERY_EXECUTION_TOOLS and not result.get("error"):
        rows = result.get("rows")
        if isinstance(rows, list) and len(rows) > 0 and "analysisNudge" not in result:
            result["analysisNudge"] = _ANALYSIS_NUDGE
    return result
