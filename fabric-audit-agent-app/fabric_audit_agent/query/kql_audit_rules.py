"""KQL audit engine — Python port of the kql-mcp plugin's rule engine.

CONTRACT
--------
This module is a NEW, self-contained diagnostic layer. It never mutates ``kql_guard.py``
(whose signatures are frozen) and takes no I/O — every function is pure and depends only on
the Python standard library (``re`` + ``dataclasses``). It statically analyses a KQL string
and returns structured findings; it never executes anything.

Public API:
    * ``QueryOutline`` / ``PipelineStep`` — structural parse dataclasses (types.ts ``QueryOutline``).
    * ``parse_outline(kql) -> QueryOutline`` — the structural parser (audit-rules ``parseQueryOutline``).
    * ``audit_kql(kql) -> dict`` — entry point: ``{outline, findings, score, grade, blocking}``.
    * ``preflight_limits(kql, expected_row_count=0) -> list[dict]`` — service-limit risk checks.
    * ``parse_kusto_error(msg) -> dict`` — Kusto error-code → actionable-suggestion mapping.
    * ``check_perf001`` / ``check_perf003_power_bi`` / ``check_correct001`` /
      ``check_correct007`` / ``check_retention`` — the individual named rule checks.

SOURCE
------
Ported from the ``kql-mcp`` TypeScript plugin (plan Phase 2 — "port the KQL audit engine"):
    * ``services/audit-rules.ts`` — PERF001 (with the EventText DAX/MDX exemption),
      CORRECT001, CORRECT007, parseQueryOutline, and the score/grade model.
    * ``index.ts`` ``kql_query_limits`` tool — the 8 pre-flight service-limit checks.
    * ``services/workspace-client.ts`` ``parseKustoError`` — the error-code mapping.
    * ``constants.ts`` — HIGH_VOLUME_TABLES, APP_INSIGHTS_TABLES, WORKSPACE_RETENTION_DAYS.

SPEC CORRECTIONS (tasks/tightening.md)
--------------------------------------
    * 25a — ``PowerBIDatasetsWorkspace`` is NOT in ``HIGH_VOLUME_TABLES``, so the stock
      PERF003 does NOT apply to it. Instead a CUSTOM ``PERF003_POWER_BI`` check enforces a
      time filter (error), and a retention check warns (not errors) when a timespan exceeds
      ``WORKSPACE_RETENTION_DAYS`` (60d) on the ``POWER_BI_TABLES`` set.
    * 26r — ``audit_kql`` blocks only on error-severity findings; warnings are surfaced but
      never block (``blocking`` is True iff any finding is error-severity).
"""

import re
from dataclasses import dataclass
from typing import Optional

# ─────────────────────────────────────────────────────────
# Constants (ported from constants.ts)
# ─────────────────────────────────────────────────────────

# 25a: PowerBIDatasetsWorkspace's retention window in ent-aas-workspace-prd.
WORKSPACE_RETENTION_DAYS = 60

# 25a: custom set — PowerBIDatasetsWorkspace is deliberately NOT in HIGH_VOLUME_TABLES.
POWER_BI_TABLES = {"PowerBIDatasetsWorkspace"}

# Workspace-based Application Insights schema (App* tables). Time column: TimeGenerated.
APP_INSIGHTS_TABLES = {
    "AppRequests",
    "AppExceptions",
    "AppDependencies",
    "AppTraces",
    "AppEvents",
    "AppAvailabilityResults",
    "AppPageViews",
    "AppBrowserTimings",
    "AppPerformanceCounters",
    "AppMetrics",
    "AppSystemEvents",
}

# Standard Azure Monitor / Log Analytics tables that ingest high volumes; a missing time
# filter on any of these is expensive. Time column: TimeGenerated.
HIGH_VOLUME_TABLES = {
    "AppServiceHTTPLogs",
    "AppServiceConsoleLogs",
    "AppServiceAppLogs",
    "AppServiceAuditLogs",
    "FunctionAppLogs",
    "ContainerLog",
    "ContainerLogV2",
    "KubePodInventory",
    "KubeEvents",
    "KubeNodeInventory",
    "KubeServices",
    "InsightsMetrics",
    "AzureActivity",
    "AzureDiagnostics",
    "AzureMetrics",
    "AzureMachineMetrics",
    "Heartbeat",
    "Perf",
    "Event",
    "Syslog",
    "W3CIISLog",
    "NetworkMonitoring",
    "AzureLoadBalancerDiagnosticLog",
    "AzureNetworkAnalytics_CL",
    "AKSAudit",
    "AKSControlPlane",
    "AKSAuthorizerLogs",
    "ApiManagementGatewayLogs",
    "StorageBlobLogs",
    "StorageQueueLogs",
    "StorageTableLogs",
    "SQLInsights",
    "AzureSQLDatabaseAdvisorRecommendationsV2",
}

# Identifiers that are keywords, not source-table names (audit-rules.ts firstTableName).
_TABLE_KEYWORDS = {
    "let", "print", "datatable", "externaldata", "union", "find", "search", "set",
}

# ─────────────────────────────────────────────────────────
# Shared parse helpers (ported from audit-rules.ts)
# ─────────────────────────────────────────────────────────


def _strip_comments(query):
    """Remove ``// line`` and ``/* block */`` comments. Mirrors audit-rules.ts stripComments."""
    q = re.sub(r"//[^\n]*", "", query)
    q = re.sub(r"/\*.*?\*/", "", q, flags=re.DOTALL)
    return q


def _first_table_name(query):
    """First non-keyword identifier — the source table — or None. Ports firstTableName."""
    stripped = _strip_comments(query).strip()
    # Strip leading `let name = ...;` bindings (multiline, dot-all) before reading the source.
    without_lets = re.sub(
        r"^\s*let\s+\w+\s*=.*?;", "", stripped, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL
    ).strip()
    source = without_lets or stripped
    m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", source)
    name = m.group(1) if m else None
    if name and name.lower() not in _TABLE_KEYWORDS:
        return name
    return None


def _detect_time_column(query):
    """Return 'timestamp', 'TimeGenerated', or None. Ports detectTimeColumn.

    Note: the ``timestamp`` / ``TimeGenerated`` probes are CASE-SENSITIVE in the source, so
    they are ported without re.IGNORECASE deliberately.
    """
    stripped = _strip_comments(query)
    if re.search(r"\btimestamp\b", stripped):
        return "timestamp"
    if re.search(r"\bTimeGenerated\b", stripped):
        return "TimeGenerated"
    table = _first_table_name(query)
    if table and table in APP_INSIGHTS_TABLES:
        return "TimeGenerated"
    return None


def _has_time_filter_present(query):
    """True when the query already carries a time-range filter. Ports hasTimeFilterPresent.

    First probe (TimeGenerated|timestamp) is case-sensitive; the rest are case-insensitive —
    faithful to the source's per-regex flags.
    """
    stripped = _strip_comments(query)
    return bool(
        re.search(r"\b(?:TimeGenerated|timestamp)\b", stripped)
        or re.search(r"\bago\s*\(", stripped, re.IGNORECASE)
        or re.search(r"\bdatetime\s*\(", stripped, re.IGNORECASE)
        or re.search(r"\bbetween\s*\(.*datetime", stripped, re.IGNORECASE)
    )


# ── EventText DAX/MDX pattern awareness (PERF001 exemption / CORRECT007 trigger) ──────────
# EventText in PowerBIDatasetsWorkspace stores raw DAX/MDX query text. Field references there
# look like 'Table'[Field] (DAX) or [Measures].[Field] (MDX). Searching these REQUIRES
# `contains` — `has` splits on non-alphanumerics and loses the bracket/quote structure, so it
# produces false positives. Heuristic: an operand is a DAX/MDX pattern iff it contains a
# square bracket. Ordinary token searches never do, so the exemption is precise.


def _operand_is_event_text_pattern(operand):
    return "[" in operand or "]" in operand


def _line_has_event_text_contains(line):
    """True when a line's ``contains "<operand>"`` operand is a bracketed EventText pattern."""
    m = re.search(r"""\bcontains\b\s*(["'])((?:\\.|(?!\1).)*)\1""", _strip_comments(line), re.IGNORECASE)
    return m is not None and _operand_is_event_text_pattern(m.group(2) or "")


def _line_has_improvised_event_text_filter(line):
    """True when a line filters EventText WITHOUT an authoritative bracketed pattern.

    Mirror-inverse of ``_line_has_event_text_contains``: bracketed contains is cleared as
    authoritative; any other EventText filter operator (bare contains / has / has_any / == /
    =~) is improvised. Consumed only by CORRECT007. Ports lineHasImprovisedEventTextFilter.
    """
    s = _strip_comments(line)
    if not re.search(r"\bEventText\b", s, re.IGNORECASE):
        return False
    if _line_has_event_text_contains(s):
        return False
    return re.search(r"\bEventText\b\s*(?:contains|has(?:_any)?|==|=~)", s, re.IGNORECASE) is not None


# ─────────────────────────────────────────────────────────
# Structural parse — QueryOutline (types.ts / 24h)
# ─────────────────────────────────────────────────────────


@dataclass
class PipelineStep:
    """One pipe step (``| operator ...``) of a KQL query. Ports types.ts PipelineStep."""

    operator: str  # operator keyword, lower-cased, e.g. "where", "summarize", "join"
    line: int      # 1-based line number of this pipe step in the source


@dataclass
class QueryOutline:
    """Structural outline of a KQL query, produced without executing it. Ports QueryOutline."""

    sourceTable: Optional[str]
    letBindings: list
    pipelineSteps: list
    hasTimeFilter: bool
    hasRowLimit: bool
    hasSummarize: bool
    hasJoin: bool
    estimatedDataSource: str  # "log-analytics" | "app-insights" | "unknown"


def parse_outline(kql):
    """Produce a :class:`QueryOutline` for a KQL string. Ports parseQueryOutline (24h)."""
    stripped = _strip_comments(kql)
    lines = re.split(r"\r?\n", kql)

    source_table = _first_table_name(kql)

    let_bindings = [m.group(1) for m in re.finditer(r"\blet\s+(\w+)\s*=", stripped, re.IGNORECASE)
                    if m.group(1)]

    pipeline_steps = []
    for i, line in enumerate(lines):
        m = re.match(r"\|\s*([a-z][-a-z]*)", line.strip(), re.IGNORECASE)
        if m:
            pipeline_steps.append(PipelineStep(operator=m.group(1).lower(), line=i + 1))

    has_time_filter = _has_time_filter_present(kql)
    has_row_limit = bool(re.search(r"\|\s*(?:take|limit|top)\s+\d+", stripped, re.IGNORECASE))
    has_summarize = bool(re.search(r"\|\s*summarize\b", stripped, re.IGNORECASE))
    has_join = bool(re.search(r"\|\s*join\b", stripped, re.IGNORECASE))

    time_col = _detect_time_column(kql)
    if time_col == "timestamp":
        estimated = "app-insights"
    elif time_col == "TimeGenerated":
        estimated = "log-analytics"
    elif source_table is not None and source_table in APP_INSIGHTS_TABLES:
        estimated = "app-insights"
    elif source_table is not None and source_table in HIGH_VOLUME_TABLES:
        estimated = "log-analytics"
    else:
        estimated = "unknown"

    return QueryOutline(
        sourceTable=source_table,
        letBindings=let_bindings,
        pipelineSteps=pipeline_steps,
        hasTimeFilter=has_time_filter,
        hasRowLimit=has_row_limit,
        hasSummarize=has_summarize,
        hasJoin=has_join,
        estimatedDataSource=estimated,
    )


# ─────────────────────────────────────────────────────────
# Deterministic fixes (ported from audit-rules.ts rule.fix functions)
# ─────────────────────────────────────────────────────────


def _fix_perf001(query):
    """Rewrite ``contains "x"`` → ``has "x"``, leaving bracketed EventText operands untouched.

    Only rewrites ``contains`` immediately followed by a string literal (leaves ``!contains``,
    ``contains~``, ``contains_cs`` alone). Ports PERF001.fix, incl. the bracket exemption.
    """

    def repl(m):
        ws, quote, operand = m.group(1), m.group(2), m.group(3)
        if _operand_is_event_text_pattern(operand):
            return m.group(0)
        return f"has{ws}{quote}{operand}{quote}"

    return re.sub(
        r"""\bcontains\b(\s*)(["'])((?:\\.|(?!\2).)*)\2""", repl, query, flags=re.IGNORECASE
    )


def _fix_correct001(query):
    """Rewrite ``col == null`` → ``isnull(col)`` and ``col != null`` → ``isnotnull(col)``.

    Simple identifier LHS only; complex expressions need manual correction. Ports CORRECT001.fix.
    """
    query = re.sub(r"\b(\w+)\s*==\s*null\b", r"isnull(\1)", query, flags=re.IGNORECASE)
    query = re.sub(r"\b(\w+)\s*!=\s*null\b", r"isnotnull(\1)", query, flags=re.IGNORECASE)
    return query


def _fix_perf003(query):
    """Insert ``| where TimeGenerated > ago(24h)`` before the first pipe. Ports PERF003.fix."""
    if _has_time_filter_present(query):
        return query
    time_col = _detect_time_column(query) or "TimeGenerated"
    q_lines = re.split(r"\r?\n", query)
    insertion = f"  | where {time_col} > ago(24h)"
    first_pipe_idx = next((i for i, l in enumerate(q_lines) if l.strip().startswith("|")), -1)
    if first_pipe_idx == -1:
        return query.rstrip() + "\n" + insertion
    result = list(q_lines)
    result.insert(first_pipe_idx, insertion)
    return "\n".join(result)


def _finding(rule_id, severity, message, suggestion, corrected=None):
    f = {"ruleId": rule_id, "severity": severity, "message": message, "suggestion": suggestion}
    if corrected is not None:
        f["corrected"] = corrected
    return f


# ─────────────────────────────────────────────────────────
# Named rule checks
# ─────────────────────────────────────────────────────────


def check_perf001(kql):
    """PERF001 — ``contains`` on a string literal should be ``has`` (10–100× faster).

    Exempts EventText DAX/MDX patterns (operand contains ``[`` or ``]``), where ``contains``
    is REQUIRED and ``has`` would be incorrect. warning severity. Ports PERF001.check.
    """
    findings = []
    corrected = None
    for line in re.split(r"\r?\n", kql):
        if re.search(r"""\bcontains\b\s*["']""", _strip_comments(line), re.IGNORECASE) \
                and not _line_has_event_text_contains(line):
            if corrected is None:
                corrected = _fix_perf001(kql)
            findings.append(_finding(
                "PERF001", "warning",
                "`contains` does a full substring scan. `has` uses the inverted index and is "
                "10-100x faster on large tables.",
                'Replace `contains "value"` with `has "value"` when searching for whole tokens.',
                corrected=corrected,
            ))
    return findings


def check_perf003_power_bi(kql):
    """PERF003_POWER_BI (custom, 25a) — a ``PowerBIDatasetsWorkspace`` query with no time filter.

    PowerBIDatasetsWorkspace is NOT in HIGH_VOLUME_TABLES so stock PERF003 never fires on it;
    this custom check enforces the same time-filter requirement at error severity, mirroring
    how the plugin blocks kql_execute.
    """
    table = _first_table_name(kql)
    if table not in POWER_BI_TABLES:
        return []
    if _has_time_filter_present(kql):
        return []
    return [_finding(
        "PERF003_POWER_BI", "error",
        "`PowerBIDatasetsWorkspace` holds high-volume Power BI query telemetry. Querying "
        "without a time filter scans the full retention window and will be slow and expensive.",
        "Add a time filter as the first `where`: `| where TimeGenerated > ago(24h)`",
        corrected=_fix_perf003(kql),
    )]


def check_correct001(kql):
    """CORRECT001 — ``col == null`` / ``col != null`` always evaluates false in KQL. error."""
    findings = []
    corrected = None
    for line in re.split(r"\r?\n", kql):
        if re.search(r"==\s*null\b|!=\s*null\b", _strip_comments(line), re.IGNORECASE):
            if corrected is None:
                corrected = _fix_correct001(kql)
            findings.append(_finding(
                "CORRECT001", "error",
                "In KQL, `column == null` always evaluates to `false`. Null checks require "
                "`isnull()` or `isnotnull()`.",
                "Replace `col == null` with `isnull(col)` and `col != null` with `isnotnull(col)`.",
                corrected=corrected,
            ))
    return findings


def check_correct007(kql):
    """CORRECT007 — a hand-authored EventText filter on ``PowerBIDatasetsWorkspace``. error.

    Only bracketed DAX (``'Table'[Field]``) / MDX (``[Measures].[Field]``) patterns produced by
    the field resolver are correct. Any improvised filter (bare display name, ``has``,
    ``has_any``, ``==``, ``=~``, invented underscore variants) is blocked. No auto-fix — the
    correct action is to call the field resolver with the field name. Ports CORRECT007.check.
    """
    if _first_table_name(kql) != "PowerBIDatasetsWorkspace":
        return []
    findings = []
    for line in re.split(r"\r?\n", kql):
        if _line_has_improvised_event_text_filter(line):
            findings.append(_finding(
                "CORRECT007", "error",
                "Filtering `EventText` on `PowerBIDatasetsWorkspace` without a bracketed DAX/MDX "
                'pattern produces incorrect or overbroad results. A bare `EventText contains '
                '"Invoice Quantity"` matches unrelated fields; `EventText has "invoice"` matches '
                "everything containing that word; invented variants like `Invoice_Quantity` do "
                "not exist in the schema. Correct patterns always contain brackets: "
                "`EventText contains \"'Table'[Field]\"` (DAX) or "
                '`EventText contains "[Measures].[Field]"` (MDX).',
                "Remove this hand-authored EventText filter and resolve the field name to its "
                "authoritative DAX and MDX patterns via the field resolver instead.",
            ))
    return findings


def _max_ago_days(kql):
    """Largest ``ago(N unit)`` lookback in the query, expressed in days (0 if none)."""
    unit_days = {"d": 1.0, "h": 1.0 / 24.0, "m": 1.0 / 1440.0, "s": 1.0 / 86400.0}
    longest = 0.0
    for m in re.finditer(r"\bago\s*\(\s*(\d+)\s*([dhms])\s*\)", kql, re.IGNORECASE):
        days = int(m.group(1)) * unit_days[m.group(2).lower()]
        longest = max(longest, days)
    return longest


def check_retention(kql):
    """Retention (25a) — a ``PowerBIDatasetsWorkspace`` timespan > WORKSPACE_RETENTION_DAYS.

    The table retains only ``WORKSPACE_RETENTION_DAYS`` (60) days, so an ``ago()`` lookback
    beyond that returns no extra data. warning severity (NOT an error), mirroring the plugin's
    usage-query-builder ``retentionWarning``.
    """
    if _first_table_name(kql) not in POWER_BI_TABLES:
        return []
    if _max_ago_days(kql) <= WORKSPACE_RETENTION_DAYS:
        return []
    return [_finding(
        "RETENTION", "warning",
        f"`PowerBIDatasetsWorkspace` retains only {WORKSPACE_RETENTION_DAYS} days of data. A "
        "timespan beyond that scans nothing extra — the extra range returns no additional rows.",
        f"Reduce the lookback to `ago({WORKSPACE_RETENTION_DAYS}d)` or less.",
    )]


_RULE_CHECKS = (
    check_perf001,
    check_perf003_power_bi,
    check_correct001,
    check_correct007,
    check_retention,
)

# ─────────────────────────────────────────────────────────
# Scoring + entry point (26r severity contract)
# ─────────────────────────────────────────────────────────

_DEDUCTION = {"error": 25, "warning": 10, "info": 2}


def _score_and_grade(findings):
    deductions = sum(_DEDUCTION.get(f["severity"], 0) for f in findings)
    score = max(0, 100 - deductions)
    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 60:
        grade = "C"
    elif score >= 40:
        grade = "D"
    else:
        grade = "F"
    return score, grade


def audit_kql(kql):
    """Audit a KQL string. Returns ``{outline, findings, score, grade, blocking}``.

    Severity contract (26r): score = 100 - (error 25 + warning 10 + info 2), clamped to >= 0;
    grade A>=90, B>=75, C>=60, D>=40, else F. ``blocking`` is True iff any finding is
    error-severity — warnings are surfaced but never block, exactly like the plugin's
    ``kql_execute`` pre-execution gate.
    """
    findings = []
    for check in _RULE_CHECKS:
        findings.extend(check(kql))
    score, grade = _score_and_grade(findings)
    blocking = any(f["severity"] == "error" for f in findings)
    return {
        "outline": parse_outline(kql),
        "findings": findings,
        "score": score,
        "grade": grade,
        "blocking": blocking,
    }


# ─────────────────────────────────────────────────────────
# Pre-flight service-limit checks (index.ts kql_query_limits / 24f)
# ─────────────────────────────────────────────────────────

_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _risk(check, level, message):
    return {"check": check, "level": level, "message": message}


def preflight_limits(kql, expected_row_count=0):
    """Static pre-flight of a KQL query against Azure Monitor / Log Analytics service limits.

    Ports index.ts ``kql_query_limits`` — the 8 checks, returned sorted HIGH → MEDIUM → LOW:
      1. no take/summarize                → HIGH (Result Truncation)
      2. expected_row_count > 400k        → HIGH (Result Truncation)   [mutually exclusive w/ 1]
      3. no project & no summarize        → MEDIUM (Data Size)
      4. no time filter                   → HIGH (Timeout)
      5. join without time filter         → HIGH (Memory / E_RUNAWAY_QUERY)
      6. make-series                      → LOW (Memory)
      7. > 5 unions                       → MEDIUM (Query Complexity)
      8. > 10 lets                        → MEDIUM (Query Complexity)
      9. `set notruncation` without take  → HIGH (Result Truncation Override)

    Note: this uses its OWN time-filter probe (``ago(`` or ``TimeGenerated|timestamp >``),
    distinct from and stricter than the audit engine's ``_has_time_filter_present``.
    """
    stripped = _strip_comments(kql)
    risks = []

    has_take = bool(re.search(r"\|\s*(?:take|limit|top)\s+\d+", stripped, re.IGNORECASE))
    has_summarize = bool(re.search(r"\|\s*summarize\b", stripped, re.IGNORECASE))
    has_project = bool(re.search(r"\|\s*project\b", stripped, re.IGNORECASE))
    has_time_filter = bool(
        re.search(r"\bago\s*\(|\b(?:TimeGenerated|timestamp)\s*>", stripped, re.IGNORECASE)
    )

    if not has_take and not has_summarize:
        risks.append(_risk(
            "Result Truncation", "HIGH",
            "No row limit or aggregation - query could return up to 500,000 rows (the service cap).",
        ))
    elif (expected_row_count or 0) > 400_000:
        risks.append(_risk(
            "Result Truncation", "HIGH",
            f"Expected {expected_row_count:,} rows is near the 500,000-row limit.",
        ))

    if not has_project and not has_summarize:
        risks.append(_risk(
            "Data Size", "MEDIUM",
            "No column projection - all columns returned. Wide tables can hit the 64 MB size limit.",
        ))

    if not has_time_filter:
        risks.append(_risk(
            "Timeout", "HIGH",
            "No time filter detected. Queries without time filters scan full retention (up to "
            "2 years). 4-minute timeout is likely to trigger.",
        ))

    has_join = bool(re.search(r"\|\s*join\b", stripped, re.IGNORECASE))
    has_make_series = bool(re.search(r"\|\s*make-series\b", stripped, re.IGNORECASE))
    if has_join and not has_time_filter:
        risks.append(_risk(
            "Memory (E_RUNAWAY_QUERY)", "HIGH",
            "Join without time filter may load too many rows into memory and trigger E_RUNAWAY_QUERY.",
        ))
    if has_make_series:
        risks.append(_risk(
            "Memory", "LOW",
            "`make-series` holds the full series in memory. Large time windows with fine "
            "granularity can cause memory pressure.",
        ))

    union_count = len(re.findall(r"\bunion\b", stripped, re.IGNORECASE))
    if union_count > 5:
        risks.append(_risk(
            "Query Complexity", "MEDIUM",
            f"{union_count} union operators detected. Deep union chains can exceed query plan "
            "size limits.",
        ))

    let_count = len(re.findall(r"\blet\s+\w+\s*=", stripped, re.IGNORECASE))
    if let_count > 10:
        risks.append(_risk(
            "Query Complexity", "MEDIUM",
            f"{let_count} let statements detected. Many nested let bindings can cause query plan "
            "failures.",
        ))

    if re.search(r"\bset\s+notruncation", stripped, re.IGNORECASE) and not has_take:
        risks.append(_risk(
            "Result Truncation Override", "HIGH",
            "`set notruncation` removes the 500k-row / 64 MB limit without any compensating row limit.",
        ))

    risks.sort(key=lambda r: _SEVERITY_ORDER.get(r["level"], 3))
    return risks


# ─────────────────────────────────────────────────────────
# Kusto error-code parser (workspace-client.ts parseKustoError)
# ─────────────────────────────────────────────────────────


def parse_kusto_error(msg):
    """Map a raw Kusto/Log Analytics error message to ``{code, suggestions}``.

    Ports workspace-client.ts ``parseKustoError``. ``code`` is the canonical error family;
    ``suggestions`` is an ordered list of actionable fixes. Unknown errors return code
    ``"UNKNOWN"`` with the raw message as the single suggestion.
    """
    raw = str(msg)
    low = raw.lower()

    if "e_query_result_set_too_large" in low:
        return {
            "code": "E_QUERY_RESULT_SET_TOO_LARGE",
            "suggestions": [
                "Add | take 10000 to limit rows",
                "Add | summarize to aggregate before returning",
                "Add | project to drop unnecessary columns",
                "Narrow the time range with | where TimeGenerated > ago(1h)",
            ],
        }

    if "e_runaway_query" in low:
        return {
            "code": "E_RUNAWAY_QUERY",
            "suggestions": [
                "Add hint.shufflekey=<key> to join or summarize operators",
                "Use | where rand() < 0.1 to sample before aggregating",
                "Narrow the time range to reduce rows processed",
                "Split complex joins into smaller subqueries",
            ],
        }

    if "timed out" in low or "timeout" in low or "request took too long" in low:
        return {
            "code": "TIMEOUT",
            "suggestions": [
                "Add a time filter: | where TimeGenerated > ago(1h)",
                "Add | take 1000 to limit rows",
                "Move aggregations earlier in the pipeline",
                "Use hint.shufflekey for large join/summarize operations",
            ],
        }

    if "unauthorized" in low or "401" in low or "forbidden" in low or "403" in low:
        return {
            "code": "AUTH",
            "suggestions": [
                "Run 'az login' or 'az account set --subscription <id>' and try again.",
            ],
        }

    return {"code": "UNKNOWN", "suggestions": [raw]}
