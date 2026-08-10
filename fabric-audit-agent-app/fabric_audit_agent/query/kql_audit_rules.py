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
      ``check_correct007`` / ``check_retention`` — the original 5 named rule checks.
    * ``check_perf002`` / ``check_perf004``..``check_perf011`` / ``check_correct002``..
      ``check_correct006`` / ``check_best001``..``check_best006`` / ``check_hint001`` /
      ``check_hint002`` — Part 24a domain-relevant rule port (see below).

SOURCE
------
Ported from the ``kql-mcp`` TypeScript plugin (plan Phase 2 — "port the KQL audit engine"):
    * ``services/audit-rules.ts`` — PERF001 (with the EventText DAX/MDX exemption),
      CORRECT001, CORRECT007, parseQueryOutline, and the score/grade model.
    * ``index.ts`` ``kql_query_limits`` tool — the 8 pre-flight service-limit checks.
    * ``services/workspace-client.ts`` ``parseKustoError`` — the error-code mapping.
    * ``constants.ts`` — HIGH_VOLUME_TABLES, APP_INSIGHTS_TABLES, TIME_SERIES_TABLES,
      WORKSPACE_RETENTION_DAYS.

SPEC CORRECTIONS (tasks/tightening.md)
--------------------------------------
    * 25a — ``PowerBIDatasetsWorkspace`` is NOT in ``HIGH_VOLUME_TABLES``, so the stock
      PERF003 does NOT apply to it. Instead a CUSTOM ``PERF003_POWER_BI`` check enforces a
      time filter (error), and a retention check warns (not errors) when a timespan exceeds
      ``WORKSPACE_RETENTION_DAYS`` (60d) on the ``POWER_BI_TABLES`` set.
    * 26r — ``audit_kql`` blocks only on error-severity findings; warnings are surfaced but
      never block (``blocking`` is True iff any finding is error-severity).

PART 24a — DOMAIN-RELEVANT RULE PORT (alerting-redesign design spec, Sub-plan 5)
---------------------------------------------------------------------------------
The plugin's ``audit-rules.ts`` defines 28 rules total: PERF001-010, CORRECT001-007,
TELEMETRY001-003, BEST001-006, HINT001-002. Before this part, only 5 were ported (PERF001,
the custom PERF003_POWER_BI, CORRECT001, CORRECT007, RETENTION). This part ports the rest of
the domain-relevant subset — everything that applies to Fabric / Power BI / Log Analytics KQL:

    * PERF002, PERF004-010 — performance rules (tolower/toupper in filters, order-by without
      take/top, join without pre-filtering, bare count on high-volume tables, unmaterialized
      repeated ``let``, ``union *``, filter-on-calculated-column, high-cardinality summarize
      without a shuffle hint).
    * PERF011 (NEW — not a plugin rule ID; sourced from the kql-performance-tuner skill's
      ``optimization-patterns.md`` Pattern 7, "dcount over count(distinct)". Given a PERF01x
      slot to stay consistent with the existing PERF numbering since the plugin has no rule ID
      for this pattern.)
    * CORRECT002-006 — summarize-by-datetime-without-bin, SQL syntax in KQL, deprecated
      ``mvexpand``, wrong time column on App Insights tables, Unix-timestamp conversion in
      query instead of at ingestion.
    * BEST001-006 — no time filter, no row limit, ``render`` without ``summarize``, filter
      after ``summarize``, missing ``bin()`` on time-series tables, ``set notruncation``
      without a row limit. Auto-fix rules per the plugin: PERF001, PERF003(_POWER_BI),
      CORRECT001, BEST001, BEST002 — ``_fix_best001``/``_fix_best002`` below extend the
      existing three fix helpers.
    * HINT001-002 — zero-score-deduction advisories (no column selection on a wide/high-volume
      table; ``count`` without a time filter on a time-series table).

DELIBERATELY SKIPPED — TELEMETRY001-003 (out of domain)
    ``AppRequests``/``AppExceptions``/``AppDependencies``-specific advisories (raw-request-scan,
    missing-project-on-exceptions, missing-success-filter-on-dependencies) are classic-schema
    Application Insights ergonomics rules with no Fabric / Power BI / Log Analytics analogue in
    this agent's domain (workspace-based App* tables and PowerBIDatasetsWorkspace query
    telemetry). Per the design spec's load-bearing decision #4, they are not ported here.
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

# Fabric capacity-event telemetry in the Eventhouse KQL DB — the source table for the
# ``engine: "capacity"`` half of query_library.json (8 of 10 templates). It is not an Azure
# Monitor table, so it appeared in NONE of the sets above and no time-filter rule covered it at
# all: an unbounded CapacityEvents scan audited clean. Its time predicate is ``ingestion_time()``
# rather than TimeGenerated, which is why _has_time_filter_present must recognise that form.
CAPACITY_TABLES = {"CapacityEvents"}

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

# Tables designed for time-series analysis (constants.ts TIME_SERIES_TABLES). Used by BEST005
# (missing bin() on a summarize/render over these) and HINT002 (count without a time filter).
TIME_SERIES_TABLES = {
    "AppRequests",
    "AppDependencies",
    "AppTraces",
    "AppExceptions",
    "AppPerformanceCounters",
    "AppMetrics",
    "Perf",
    "InsightsMetrics",
    "AzureMetrics",
    "ContainerLog",
    "ContainerLogV2",
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


# A time PREDICATE, not a mention of a time column. The column-name probe stays case-sensitive
# (source behaviour) but now requires a comparison operator after the column.
_TIME_COLUMN_PREDICATE_RE = re.compile(
    r"\b(?:TimeGenerated|timestamp)\b\s*(?:[<>]=?|between\b|in\s*\(|\.\.)"
)
_INGESTION_TIME_PREDICATE_RE = re.compile(
    r"\bingestion_time\s*\(\s*\)\s*(?:[<>]=?|between\b)", re.IGNORECASE
)


def _has_time_filter_present(query):
    """True when the query already carries a time-range FILTER — a predicate, not a mention.

    Ports hasTimeFilterPresent, with the column-name probe corrected. The source's
    ``\\b(TimeGenerated|timestamp)\\b`` matched any appearance of the column, so an unbounded
    full-retention scan of the highest-volume table scored 100/A with zero findings purely
    because it projected or ``bin()``-ed the column:

        PowerBIDatasetsWorkspace | summarize cpuMs=sum(CpuTimeMs) by bin(TimeGenerated, 5m)

    Every shipped query shape names TimeGenerated, so dropping the one ``| where`` line was the
    natural error and the blocking rules stayed silent — while ``preflight_limits``, using the
    stricter probe, flagged the same query. The two now agree.
    """
    stripped = _strip_comments(query)
    return bool(
        _TIME_COLUMN_PREDICATE_RE.search(stripped)
        or _INGESTION_TIME_PREDICATE_RE.search(stripped)
        or re.search(r"\bago\s*\(", stripped, re.IGNORECASE)
        or re.search(r"\bbetween\s*\(.*datetime", stripped, re.IGNORECASE)
        or re.search(r"[<>]=?\s*datetime\s*\(", stripped, re.IGNORECASE)
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


def _fix_best001(query):
    """Insert ``| where TimeGenerated > ago(24h)`` before the first pipe. Ports BEST001.fix.

    Idempotent with ``_fix_perf003``: both insert the same shape of time filter, and both
    bail out via ``_has_time_filter_present`` when one has already run.
    """
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


def _has_row_limit_present(query):
    """True when the query already has ``take``/``limit``/``top``, ``summarize``, or ``count``."""
    stripped = _strip_comments(query)
    return bool(
        re.search(r"\|\s*(?:take|limit|top)\s+\d+", stripped, re.IGNORECASE)
        or re.search(r"\|\s*summarize\b", stripped, re.IGNORECASE)
        or re.search(r"\|\s*count\b", stripped, re.IGNORECASE)
    )


def _fix_best002(query):
    """Append ``| take 1000``. Idempotent — does nothing if a row limit already exists.

    Ports BEST002.fix.
    """
    if _has_row_limit_present(query):
        return query
    return query.rstrip() + "\n  | take 1000"


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


def check_perf003_capacity(kql):
    """PERF003_CAPACITY (custom) — a ``CapacityEvents`` query with no time filter. error.

    Same contract as PERF003_POWER_BI, for the capacity engine: CapacityEvents is a per-window
    event stream, so an unfiltered scan reads the whole Eventhouse retention. The shipped
    templates all bound it with ``ingestion_time() > ago(...)``, so the suggested fix uses that
    form rather than TimeGenerated.
    """
    if _first_table_name(kql) not in CAPACITY_TABLES:
        return []
    if _has_time_filter_present(kql):
        return []
    return [_finding(
        "PERF003_CAPACITY", "error",
        "`CapacityEvents` holds one row per capacity window. Querying without a time filter "
        "scans the full Eventhouse retention and will be slow and expensive.",
        "Add a time filter as the first `where`: `| where ingestion_time() > ago(1d)`",
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


# ─────────────────────────────────────────────────────────
# Part 24a — domain-relevant rule port (PERF002/004-011, CORRECT002-006, BEST001-006,
# HINT001-002). See the module docstring "PART 24a" section for scope + the deliberate
# TELEMETRY001-003 skip. Each check is pure and tolerant: a line/query it cannot parse simply
# produces no finding, mirroring the existing five checks above.
# ─────────────────────────────────────────────────────────


def check_perf002(kql):
    """PERF002 — ``tolower()``/``toupper()`` inside a ``where`` clause. warning.

    Re-casing inside a filter bypasses the index and forces a full column scan; ``=~`` is
    index-aware and case-insensitive already. Ports PERF002.check (also optimization-patterns.md
    Pattern 3, "=~ over tolower()").
    """
    findings = []
    for line in re.split(r"\r?\n", kql):
        if re.search(r"\b(?:tolower|toupper)\s*\(", line, re.IGNORECASE) \
                and re.search(r"\|\s*where\b", line, re.IGNORECASE):
            findings.append(_finding(
                "PERF002", "warning",
                "Re-casing inside a `where` prevents index usage and forces a full column scan.",
                'Use case-insensitive `=~` instead: `where col =~ "value"` rather than '
                '`where tolower(col) == "value"`.',
            ))
    return findings


def check_perf004(kql):
    """PERF004 — ``order by``/``sort by`` without a ``take``/``limit``/``top`` row cap. warning.

    Sorting without a limit materialises and sorts the full result set. Ports PERF004.check
    (also optimization-patterns.md Pattern 5, "top N over order by + take").
    """
    stripped = _strip_comments(kql)
    if re.search(r"\|\s*(?:order|sort)\s+by\b", stripped, re.IGNORECASE) \
            and not re.search(r"\|\s*(?:take|limit|top)\s+\d+", stripped, re.IGNORECASE):
        return [_finding(
            "PERF004", "warning",
            "Sorting without a row limit materialises and sorts every matching row before returning.",
            "Add `| take 1000` or use `| top N by column` which combines sort + limit in one pass.",
        )]
    return []


def check_perf005(kql):
    """PERF005 — ``join`` with no ``where`` filter nearby. warning.

    Joining large unfiltered tables is expensive; both sides should be reduced (filtered AND
    projected down to the columns actually needed) before the join. Ports PERF005.check (also
    optimization-patterns.md Pattern 6, "pre-filter both join sides").
    """
    lines = re.split(r"\r?\n", kql)
    findings = []
    for i, line in enumerate(lines):
        if re.search(r"\|\s*join\b", line, re.IGNORECASE):
            inner = " ".join(lines[i:i + 6])
            if not re.search(r"\bwhere\b", inner, re.IGNORECASE):
                findings.append(_finding(
                    "PERF005", "warning",
                    "Joining large unfiltered tables is expensive. Both sides should be reduced "
                    "first.",
                    "Add `| where` clauses on both the outer table and inside the join subquery "
                    "(and `| project` down to the columns the join actually needs).",
                ))
    return findings


def check_perf006(kql):
    """PERF006 — bare ``count`` on a ``HIGH_VOLUME_TABLES`` table with no ``summarize``. info.

    Running `count` on a high-volume table scans every matching row; the pre-aggregated `Usage`
    table is far cheaper for ingestion-volume questions. Ports PERF006.check.
    """
    table = _first_table_name(kql)
    if not table or table not in HIGH_VOLUME_TABLES:
        return []
    stripped = _strip_comments(kql)
    has_bare_count = bool(re.search(r"\|\s*count\b(?!\s*if\s*\()", stripped, re.IGNORECASE))
    has_summarize = bool(re.search(r"\|\s*summarize\b", stripped, re.IGNORECASE))
    if not has_bare_count or has_summarize:
        return []
    return [_finding(
        "PERF006", "info",
        f"Running `count` on `{table}` must scan every matching row. For volume analysis, the "
        "`Usage` table (pre-aggregated) is far cheaper.",
        f'Replace with: `Usage | where DataType == "{table}" | summarize TotalGB = '
        "sum(Quantity) / 1000.0 by bin(TimeGenerated, 1d)`",
    )]


def check_perf007(kql):
    """PERF007 — a ``let`` binding referenced 2+ times without ``materialize()``. warning.

    Without ``materialize()``, a repeated ``let`` re-runs the underlying computation once per
    reference. Ports PERF007.check.
    """
    stripped = _strip_comments(kql)
    for m in re.finditer(r"\blet\s+(\w+)\s*=", stripped, re.IGNORECASE):
        name = m.group(1)
        if not name:
            continue
        after_eq = stripped[m.end():].lstrip()
        if re.match(r"^materialize\s*\(", after_eq, re.IGNORECASE):
            continue
        escaped = re.escape(name)
        usage_count = len(re.findall(rf"\b{escaped}\b", stripped)) - 1
        if usage_count >= 2:
            return [_finding(
                "PERF007", "warning",
                f"When a `let` binding is referenced multiple times the underlying calculation "
                f"runs once per reference (`let {name}` is referenced {usage_count} times).",
                f"Wrap the value: `let {name} = materialize(<expression>);` to evaluate exactly "
                "once.",
            )]
    return []


def check_perf008(kql):
    """PERF008 — ``union *`` scans every table simultaneously. warning.

    Exempts the standard table-discovery idiom (``union withsource=T * | ... | distinct``).
    Ports PERF008.check.
    """
    stripped = _strip_comments(kql)
    if re.search(r"union\s+withsource\s*=\s*T\s+\*[\s\S]*?\|\s*distinct", stripped, re.IGNORECASE):
        return []
    findings = []
    for line in re.split(r"\r?\n", kql):
        if re.search(r"\bunion\s+\*", line, re.IGNORECASE):
            findings.append(_finding(
                "PERF008", "warning",
                "`union *` issues a query against every table simultaneously, causing query plan "
                "complexity to explode.",
                "Specify the exact tables you need: `union Table1, Table2` instead of `union *`.",
            ))
    return findings


def check_perf009(kql):
    """PERF009 — a ``where`` immediately after an ``extend`` filters on the calculated column. info.

    Filtering on a value created by `extend` forces every row to be computed first. Ports
    PERF009.check.
    """
    lines = re.split(r"\r?\n", kql)
    saw_extend_alias = None
    findings = []
    for line in lines:
        m = re.search(r"\|\s*extend\s+(\w+)\s*=", line, re.IGNORECASE)
        if m:
            saw_extend_alias = m.group(1)
            continue
        if saw_extend_alias is not None:
            alias = saw_extend_alias
            if re.search(r"\|\s*where\b", line, re.IGNORECASE) and alias in line:
                findings.append(_finding(
                    "PERF009", "info",
                    f"Filtering on a value created by `extend` (`{alias}`) forces every row to be "
                    "processed first.",
                    "Move the filter before `extend`, or re-express it using the original column "
                    "directly.",
                ))
            saw_extend_alias = None
    return findings


def check_perf010(kql):
    """PERF010 — high-cardinality ``summarize by`` without ``hint.shufflekey``. info.

    Grouping by a high-cardinality column (user/client/ip/session/request id) concentrates load
    on a single node. Ports PERF010.check.
    """
    findings = []
    for line in re.split(r"\r?\n", kql):
        if re.search(r"\|\s*summarize\b", line, re.IGNORECASE) \
                and re.search(r"\bby\s+(?:user|user_id|userid|client|ip|address|session|"
                               r"request_?id)", line, re.IGNORECASE) \
                and not re.search(r"hint\.shufflekey", line, re.IGNORECASE):
            findings.append(_finding(
                "PERF010", "info",
                "Grouping by high-cardinality columns concentrates load on a single node.",
                "Add the shuffle hint: `| summarize ... by hint.shufflekey=<key> <key>, ...`",
            ))
    return findings


def check_perf011(kql):
    """PERF011 (NEW — not a plugin rule ID; see module docstring) — ``count(distinct x)`` where
    an approximate ``dcount(x)`` would do. info.

    Sourced from the kql-performance-tuner skill's optimization-patterns.md Pattern 7: `dcount`
    is ~10x faster than exact `count(distinct ...)` and is usually accurate enough.
    """
    findings = []
    for line in re.split(r"\r?\n", kql):
        if re.search(r"\bcount\s*\(\s*distinct\b", line, re.IGNORECASE):
            findings.append(_finding(
                "PERF011", "info",
                "`count(distinct x)` computes an exact distinct count, which is expensive on "
                "large tables.",
                "Use `dcount(x)` for an approximate count (~10x faster, usually accurate enough) "
                "unless exactness is required.",
            ))
    return findings


def check_correct002(kql):
    """CORRECT002 — ``summarize by`` a raw datetime column without ``bin()``. warning.

    Grouping by a raw datetime produces one group per distinct timestamp value (often one per
    millisecond) — almost never what's intended, and extremely expensive. Ports CORRECT002.check.
    """
    findings = []
    for line in re.split(r"\r?\n", kql):
        if re.search(r"\|\s*summarize\b", line, re.IGNORECASE):
            has_by_datetime = bool(
                re.search(r"\bby\b.*(?:TimeGenerated|timestamp)\b", line, re.IGNORECASE)
            )
            has_bin = bool(re.search(r"\bbin\s*\(", line, re.IGNORECASE))
            if has_by_datetime and not has_bin:
                findings.append(_finding(
                    "CORRECT002", "warning",
                    "Grouping by a raw datetime produces one group per millisecond — likely not "
                    "what you want and extremely expensive.",
                    "Wrap the datetime: `| summarize count() by bin(TimeGenerated, 1h)` or "
                    "`bin(timestamp, 5m)`.",
                ))
    return findings


_SQL_SYNTAX_PATTERNS = (
    (re.compile(r"\bSELECT\b", re.IGNORECASE), "SQL `SELECT`", "Use `| project col1, col2` instead"),
    (re.compile(r"\bFROM\b(?!\s+\w[\w.]*\s*:)", re.IGNORECASE), "SQL `FROM`",
     "Table name goes at the start: `TableName | where ...`"),
    (re.compile(r"\bGROUP\s+BY\b", re.IGNORECASE), "SQL `GROUP BY`", "Use `| summarize count() by column`"),
    (re.compile(r"\bINNER\s+JOIN\b", re.IGNORECASE), "SQL `INNER JOIN`", "Use `| join kind=inner (...)` in KQL"),
)


def check_correct003(kql):
    """CORRECT003 — SQL syntax (``SELECT``/``FROM``/``GROUP BY``/``INNER JOIN``) is not valid KQL.
    error. Ports CORRECT003.check.

    BUG 3 fix: the SQL-syntax regexes must only match actual code, never text INSIDE a quoted
    string literal (e.g. ``where Url contains "select * from orders"`` is valid KQL, not SQL) —
    so string-literal CONTENTS are blanked out (``kql_guard._strip_string_literals``) on top of
    the existing comment-stripping before matching.
    """
    from .kql_guard import _strip_string_literals
    findings = []
    for pat, label, fix in _SQL_SYNTAX_PATTERNS:
        for line in re.split(r"\r?\n", kql):
            if pat.search(_strip_string_literals(_strip_comments(line))):
                findings.append(_finding(
                    "CORRECT003", "error", f"{label} is not valid KQL.", fix,
                ))
    return findings


def check_correct004(kql):
    """CORRECT004 — deprecated ``mvexpand`` (use ``mv-expand``). warning. Ports CORRECT004.check."""
    findings = []
    for line in re.split(r"\r?\n", kql):
        if re.search(r"\bmvexpand\b", line, re.IGNORECASE) and not re.search(r"\bmv-expand\b", line, re.IGNORECASE):
            findings.append(_finding(
                "CORRECT004", "warning",
                "`mvexpand` is deprecated and may be removed.", "Replace with `mv-expand`.",
            ))
    return findings


def check_correct005(kql):
    """CORRECT005 — ``timestamp`` used on a workspace-based App Insights table. error.

    Workspace-based App* tables use `TimeGenerated`, not the classic-schema `timestamp` column;
    a query using `timestamp` against one of these will fail or silently return no rows. Ports
    CORRECT005.check.
    """
    from .kql_guard import _strip_string_literals
    table = _first_table_name(kql)
    if not table or table not in APP_INSIGHTS_TABLES:
        return []
    stripped = _strip_string_literals(_strip_comments(kql))
    if not re.search(r"\btimestamp\b", stripped):
        return []
    return [_finding(
        "CORRECT005", "error",
        f"This workspace uses the workspace-based Application Insights schema (App* tables), "
        f"where the time column is `TimeGenerated`. `timestamp` does not exist on `{table}`, so "
        "the query will fail or return no rows.",
        "Replace `timestamp` with `TimeGenerated`: `| where TimeGenerated > ago(24h)`",
    )]


def check_correct006(kql):
    """CORRECT006 — Unix-timestamp conversion inside the query. info.

    Converting Unix timestamps on every query forces a per-row type conversion; converting once
    at ingestion (via an update policy) is cheaper. Ports CORRECT006.check.
    """
    findings = []
    for line in re.split(r"\r?\n", kql):
        if re.search(r"unixtime_(?:milliseconds|seconds|microseconds|nanoseconds)_todatetime\s*\(",
                      line, re.IGNORECASE):
            findings.append(_finding(
                "CORRECT006", "info",
                "Converting Unix timestamps on every query forces a per-row type conversion. "
                "Convert once at ingestion via an update policy.",
                "Use an ingestion update policy to store time as `datetime` at write time.",
            ))
    return findings


def check_best001(kql):
    """BEST001 — no time filter anywhere in the query. info. Ports BEST001.check."""
    if _has_time_filter_present(kql):
        return []
    return [_finding(
        "BEST001", "info",
        "Queries without a time filter scan the full retention window of every table involved.",
        "Add `| where TimeGenerated > ago(24h)` (Log Analytics) or `| where timestamp > ago(24h)` "
        "(App Insights).",
        corrected=_fix_best001(kql),
    )]


def check_best002(kql):
    """BEST002 — no row limit (``take``/``limit``/``top``, ``summarize``, or ``count``). info.
    Ports BEST002.check.
    """
    if _has_row_limit_present(kql):
        return []
    return [_finding(
        "BEST002", "info",
        "Without `take`/`limit`, large tables return up to the service cap (500k rows).",
        "Add `| take 1000` for exploration, or use `| summarize` to aggregate before returning.",
        corrected=_fix_best002(kql),
    )]


def check_best003(kql):
    """BEST003 — ``render`` without a preceding ``summarize`` produces an unreadable chart. info.
    Ports BEST003.check.
    """
    stripped = _strip_comments(kql)
    if re.search(r"\|\s*render\b", stripped, re.IGNORECASE) \
            and not re.search(r"\|\s*summarize\b", stripped, re.IGNORECASE):
        return [_finding(
            "BEST003", "info",
            "Rendering raw rows produces an unreadable chart. Aggregate first.",
            "Add `| summarize count() by bin(TimeGenerated, 1h)` before `| render timechart`.",
        )]
    return []


def check_best004(kql):
    """BEST004 — a ``where`` after ``summarize`` filters post-aggregation. info.

    Filtering after aggregation means every row must be aggregated before the filter applies.
    Ports BEST004.check.
    """
    saw_summarize = False
    findings = []
    for line in re.split(r"\r?\n", kql):
        if re.search(r"\|\s*summarize\b", line, re.IGNORECASE):
            saw_summarize = True
            continue
        if saw_summarize and re.search(r"\|\s*where\b", line, re.IGNORECASE):
            findings.append(_finding(
                "BEST004", "info",
                "Filtering after aggregation means all rows must be aggregated before the filter "
                "is applied.",
                "Move `| where` clauses before `| summarize` to reduce the rows being aggregated.",
            ))
            saw_summarize = False
    return findings


def check_best005(kql):
    """BEST005 — a ``TIME_SERIES_TABLES`` table aggregated/rendered without ``bin()``. warning.

    Aggregating time-series data without `bin()` groups by exact timestamp instead of a time
    bucket. Ports BEST005.check.
    """
    table = _first_table_name(kql)
    if not table or table not in TIME_SERIES_TABLES:
        return []
    stripped = _strip_comments(kql)
    has_summarize = bool(re.search(r"\|\s*summarize\b", stripped, re.IGNORECASE))
    has_bin = bool(re.search(r"\bbin\s*\(", stripped, re.IGNORECASE))
    has_render = bool(re.search(r"\|\s*render\b", stripped, re.IGNORECASE))
    if (has_summarize or has_render) and not has_bin:
        return [_finding(
            "BEST005", "warning",
            f"`{table}` is designed for time-series analysis. Aggregating without `bin()` groups "
            "by exact timestamp.",
            "Use `bin(timestamp, 5m)` or `bin(TimeGenerated, 1h)` in your `summarize by` clause.",
        )]
    return []


def check_best006(kql):
    """BEST006 — ``set notruncation`` with no compensating row limit. warning.

    `set notruncation` removes the 500,000-row / 64 MB result limit; without a `take` or
    `summarize`, the query could return unbounded data. Ports BEST006.check.
    """
    stripped = _strip_comments(kql)
    if re.search(r"\bset\s+notruncation\s*;", stripped, re.IGNORECASE) \
            and not re.search(r"\|\s*(?:take|limit|top)\s+\d+", stripped, re.IGNORECASE) \
            and not re.search(r"\|\s*summarize\b", stripped, re.IGNORECASE):
        return [_finding(
            "BEST006", "warning",
            "`set notruncation` removes the 500,000-row / 64 MB result limit. Without a `take` "
            "or `summarize`, this query could return unbounded data.",
            "Always pair `set notruncation` with `| take N` or use `| summarize`. For bulk "
            "export, use the `.export` command instead.",
        )]
    return []


def check_hint001(kql):
    """HINT001 — no column selection on a ``HIGH_VOLUME_TABLES`` table. hint (0 deduction).

    Advisory only — returning all columns from a wide, high-volume table increases data
    transfer and can make results harder to interpret. Ports HINT001.check.
    """
    table = _first_table_name(kql)
    if not table or table not in HIGH_VOLUME_TABLES:
        return []
    stripped = _strip_comments(kql)
    if not re.search(r"\|\s*project\b", stripped, re.IGNORECASE) \
            and not re.search(r"\|\s*summarize\b", stripped, re.IGNORECASE):
        return [_finding(
            "HINT001", "hint",
            f"`{table}` is a high-volume table with many columns. Returning all columns "
            "increases data transfer and can make results harder to interpret.",
            "Add `| project col1, col2, col3` to select only the columns you need.",
        )]
    return []


def check_hint002(kql):
    """HINT002 — ``count`` without a time filter on a ``TIME_SERIES_TABLES`` table. hint
    (0 deduction). Ports HINT002.check.
    """
    table = _first_table_name(kql)
    if not table or table not in TIME_SERIES_TABLES:
        return []
    stripped = _strip_comments(kql)
    if re.search(r"\|\s*count\b(?!\s*if\s*\()", stripped, re.IGNORECASE) \
            and not _has_time_filter_present(kql):
        return [_finding(
            "HINT002", "hint",
            f"Counting all rows in `{table}` without a time filter gives the total since "
            "ingestion, which is rarely the intended metric.",
            "Add a time filter to scope the count: `| where TimeGenerated > ago(24h) | count`",
        )]
    return []


_RULE_CHECKS = (
    check_perf001,
    check_perf002,
    check_perf003_power_bi,
    check_perf003_capacity,
    check_perf004,
    check_perf005,
    check_perf006,
    check_perf007,
    check_perf008,
    check_perf009,
    check_perf010,
    check_perf011,
    check_correct001,
    check_correct002,
    check_correct003,
    check_correct004,
    check_correct005,
    check_correct006,
    check_correct007,
    check_retention,
    check_best001,
    check_best002,
    check_best003,
    check_best004,
    check_best005,
    check_best006,
    check_hint001,
    check_hint002,
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


# ─────────────────────────────────────────────────────────
# Live-query failure classifier (alerting-redesign Sub-plan 4, Task 4d / audit finding 25e)
# ─────────────────────────────────────────────────────────

# Genuine "this table/entity/column doesn't exist" semantics -- checked FIRST and takes
# precedence over every other family. A not-found MUST NOT be reported as a FAILURE (auth /
# throttled / timeout / network), and a FAILURE must never be phrased as "table doesn't exist" /
# "no data" -- that conflation is exactly what this classifier exists to prevent (25e).
_NOT_FOUND_MARKERS = (
    "entitynotfound", "entity not found", "could not be resolved",
    "failed to resolve table", "failed to resolve entity",
    "failed to resolve table or column expression named",
    "table not found", "no such table", "unknown table",
    "cannot find table", "invalid table name", "sem0100",
)

# HTTP 429/503/504 or a "throttled" message -- the same shape ``adapters/clients.py``
# ``_la_is_transient`` treats as retryable.
_THROTTLE_RE = re.compile(r"\b(429|503|504)\b|throttl", re.IGNORECASE)

# Connection-level failures not already covered by ``investigation.xmla_classify``'s
# "connection-drop" markers (kept here so plain socket/DNS errors from non-XMLA clients --
# e.g. ``requests``/``pyodbc`` -- still classify as "network").
_NETWORK_MARKERS = (
    "name or service not known", "temporary failure in name resolution",
    "getaddrinfo failed", "max retries exceeded", "connection aborted",
    "failed to establish a new connection",
)


def classify_live_query_error(exc):
    """Classify a caught live-query exception (Kusto/Log Analytics/XMLA/SQL) into one of
    ``"not-found"`` / ``"auth"`` / ``"throttled"`` / ``"timeout"`` / ``"network"`` / ``"unknown"``.

    This is the SINGLE SOURCE OF TRUTH for handler-layer error classification (tools.py grounding
    handlers such as ``describe_source_handler``) -- it deliberately REUSES rather than reinvents:
    ``parse_kusto_error`` above for the TIMEOUT/AUTH text families, and
    ``investigation.xmla_classify.classify_xmla_error`` for its auth/connection-drop markers.
    Only the "not-found" and "throttled" families (neither of which those two cover) are new here.

    Distinguishing a genuine "table/entity not found" from an AUTH/throttle/network/timeout
    FAILURE matters because collapsing them into one generic error misreports a token expiry or
    a transient 503 as "no data" -- audit finding 25e. Text-only matching against ``str(exc)``
    (and the exception's type name for timeouts); never raises. Accepts an ``Exception`` instance
    or a plain string."""
    raw = str(exc)
    low = raw.lower()
    type_name = type(exc).__name__.lower() if isinstance(exc, BaseException) else ""

    if any(marker in low for marker in _NOT_FOUND_MARKERS):
        return "not-found"

    if _THROTTLE_RE.search(raw):
        return "throttled"

    if "timeout" in type_name or parse_kusto_error(raw)["code"] == "TIMEOUT":
        return "timeout"

    from ..investigation.xmla_classify import classify_xmla_error
    xmla_class = classify_xmla_error(raw)
    if xmla_class == "auth" or parse_kusto_error(raw)["code"] == "AUTH":
        return "auth"
    if xmla_class == "connection-drop" or any(marker in low for marker in _NETWORK_MARKERS):
        return "network"

    return "unknown"
