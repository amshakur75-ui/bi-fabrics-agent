"""Tests for the ported KQL audit engine (fabric_audit_agent.query.kql_audit_rules).

Mirrors the plugin's audit-rules.test.ts cases for the ported rules (PERF001 + its EventText
exemption, CORRECT001, CORRECT007) and adds cases for the custom 25a checks (PERF003_POWER_BI,
retention), the QueryOutline parser, the pre-flight limit checks, and the Kusto error mapper.
"""

from fabric_audit_agent.query.kql_audit_rules import (
    POWER_BI_TABLES,
    WORKSPACE_RETENTION_DAYS,
    PipelineStep,
    QueryOutline,
    audit_kql,
    check_correct001,
    check_correct007,
    check_perf001,
    check_perf003_power_bi,
    check_retention,
    parse_kusto_error,
    parse_outline,
    preflight_limits,
)


def _rule_ids(findings):
    return [f["ruleId"] for f in findings]


def _fires(kql, rule_id):
    return any(f["ruleId"] == rule_id for f in audit_kql(kql)["findings"])


# ── constants sanity ─────────────────────────────────────────────────────────


def test_constants():
    assert WORKSPACE_RETENTION_DAYS == 60
    assert POWER_BI_TABLES == {"PowerBIDatasetsWorkspace"}


# ── PERF001: contains vs has ──────────────────────────────────────────────────


def test_perf001_fires_on_contains_string_literal():
    assert _fires('requests | where message contains "error"', "PERF001")
    assert _fires("Table | where col contains 'value'", "PERF001")


def test_perf001_does_not_fire_on_has_or_column_reference():
    assert not _fires('Table | where col has "value"', "PERF001")
    # contains a column reference (no string literal) — not flagged
    assert not _fires("Table | where col contains otherCol", "PERF001")


def test_perf001_severity_is_warning():
    findings = check_perf001('Table | where msg contains "err"')
    assert findings and findings[0]["severity"] == "warning"


def test_perf001_ignores_contains_inside_comment():
    assert not _fires('Table | where col has "val" // use has, not contains "val"', "PERF001")


# ── PERF001: EventText DAX/MDX exemption ──────────────────────────────────────


def test_perf001_exempts_dax_bracket_pattern():
    q = 'PowerBIDatasetsWorkspace | where EventText contains "\'Invoice Sales\'[Invoice Quantity]"'
    assert not _fires(q, "PERF001")


def test_perf001_exempts_mdx_measure_pattern():
    q = 'PowerBIDatasetsWorkspace | where EventText contains "[Measures].[Invoice Quantity]"'
    assert not _fires(q, "PERF001")


def test_perf001_still_fires_on_ordinary_contains_alongside_exempt_line():
    q = (
        "PowerBIDatasetsWorkspace\n"
        '| where EventText contains "[Measures].[Invoice Quantity]"\n'
        '| where Message contains "error"'
    )
    assert _fires(q, "PERF001")


def test_perf001_corrected_rewrites_ordinary_but_not_bracketed():
    # ordinary contains → corrected uses `has`
    findings = check_perf001('Table | where Message contains "error"')
    assert findings and 'has "error"' in findings[0]["corrected"]

    # bracketed EventText contains: rule does not fire, so corrected is never produced,
    # and the fix path (exercised via a mixed query) must leave the bracket operand alone.
    q = (
        "PowerBIDatasetsWorkspace\n"
        '| where EventText contains "\'Invoice Sales\'[Invoice Quantity]"\n'
        '| where Message contains "error"'
    )
    corrected = check_perf001(q)[0]["corrected"]
    assert 'contains "\'Invoice Sales\'[Invoice Quantity]"' in corrected
    assert 'has "\'Invoice Sales\'[Invoice Quantity]"' not in corrected
    assert 'has "error"' in corrected


# ── PERF003_POWER_BI (custom, 25a) ────────────────────────────────────────────


def test_perf003_power_bi_blocks_no_time_filter():
    q = 'PowerBIDatasetsWorkspace | where ArtifactName == "Ent-Reporting-DTC"'
    findings = check_perf003_power_bi(q)
    assert len(findings) == 1
    assert findings[0]["severity"] == "error"
    # audit-level: it is a blocking finding
    assert audit_kql(q)["blocking"] is True


def test_perf003_power_bi_corrected_inserts_time_filter():
    q = 'PowerBIDatasetsWorkspace\n| where ArtifactName == "x"'
    corrected = check_perf003_power_bi(q)[0]["corrected"]
    assert "where TimeGenerated > ago(24h)" in corrected


def test_perf003_power_bi_no_fire_with_time_filter():
    q = "PowerBIDatasetsWorkspace | where TimeGenerated > ago(30d) | summarize count()"
    assert check_perf003_power_bi(q) == []


def test_perf003_power_bi_scoped_to_table():
    # A different table missing a time filter is out of PERF003_POWER_BI's scope.
    assert check_perf003_power_bi('ContainerLog | where Level == "Error"') == []


# ── CORRECT001: == null ───────────────────────────────────────────────────────


def test_correct001_fires_on_equality_null():
    assert _fires("Table | where col == null", "CORRECT001")
    assert _fires("Table | where col != null", "CORRECT001")


def test_correct001_does_not_fire_on_isnull():
    assert not _fires("Table | where isnull(col)", "CORRECT001")
    assert not _fires("Table | where isnotnull(col)", "CORRECT001")


def test_correct001_error_severity_and_corrected():
    findings = check_correct001("Table | where x == null")
    assert findings[0]["severity"] == "error"
    assert "isnull(x)" in findings[0]["corrected"]
    findings2 = check_correct001("Table | where y != null")
    assert "isnotnull(y)" in findings2[0]["corrected"]


def test_correct001_ignores_null_inside_comment():
    assert not _fires('Table | where col == "test" // col == null is not valid', "CORRECT001")


# ── CORRECT007: hand-authored EventText filter ────────────────────────────────


def test_correct007_fires_on_improvised_filters():
    assert _fires('PowerBIDatasetsWorkspace\n| where EventText contains "Invoice Quantity"', "CORRECT007")
    assert _fires('PowerBIDatasetsWorkspace\n| where EventText has "invoice"', "CORRECT007")
    assert _fires(
        'PowerBIDatasetsWorkspace\n| where EventText has_any ("invoice", "Invoice", "INVOICE")',
        "CORRECT007",
    )
    assert _fires('PowerBIDatasetsWorkspace\n| where EventText contains "Invoice_Quantity"', "CORRECT007")


def test_correct007_error_severity():
    findings = check_correct007('PowerBIDatasetsWorkspace\n| where EventText contains "Invoice Quantity"')
    assert findings and findings[0]["severity"] == "error"


def test_correct007_does_not_fire_on_bracketed_patterns():
    assert not _fires(
        'PowerBIDatasetsWorkspace\n| where EventText contains "\'Invoice Sales\'[Invoice Quantity]"',
        "CORRECT007",
    )
    assert not _fires(
        'PowerBIDatasetsWorkspace\n| where EventText contains "[Measures].[Invoice Quantity]"',
        "CORRECT007",
    )


def test_correct007_scoped_to_power_bi_table():
    # Same improvised filter on a different table is out of scope.
    assert not _fires('AppRequests\n| where EventText contains "Invoice Quantity"', "CORRECT007")
    # PowerBIDatasetsWorkspace with no EventText filter at all: no CORRECT007.
    q = (
        "PowerBIDatasetsWorkspace\n"
        "| where TimeGenerated > ago(30d)\n"
        "| where ArtifactName == 'Ent-Reporting-Sales'\n"
        "| summarize count() by ExecutingUser"
    )
    assert not _fires(q, "CORRECT007")


def test_correct007_and_perf001_consistency():
    # Bracketed EventText: neither fires.
    q_ok = 'PowerBIDatasetsWorkspace\n| where EventText contains "\'Invoice Sales\'[Invoice Quantity]"'
    assert not _fires(q_ok, "PERF001")
    assert not _fires(q_ok, "CORRECT007")
    # has operator: CORRECT007 fires, PERF001 does NOT (has is not contains).
    q_has = 'PowerBIDatasetsWorkspace\n| where EventText has "invoice"'
    assert _fires(q_has, "CORRECT007")
    assert not _fires(q_has, "PERF001")


# ── Retention (25a) ───────────────────────────────────────────────────────────


def test_retention_warns_over_60_days():
    q = 'PowerBIDatasetsWorkspace | where TimeGenerated > ago(90d) | summarize count()'
    findings = check_retention(q)
    assert len(findings) == 1
    assert findings[0]["severity"] == "warning"
    assert findings[0]["ruleId"] == "RETENTION"


def test_retention_is_warning_not_error_at_audit_level():
    # A >60d PowerBIDatasetsWorkspace query with a time filter must NOT be blocking.
    q = "PowerBIDatasetsWorkspace | where TimeGenerated > ago(90d) | summarize count()"
    result = audit_kql(q)
    assert "RETENTION" in _rule_ids(result["findings"])
    assert result["blocking"] is False


def test_retention_no_warn_at_or_below_60_days():
    assert check_retention("PowerBIDatasetsWorkspace | where TimeGenerated > ago(30d) | count") == []
    assert check_retention("PowerBIDatasetsWorkspace | where TimeGenerated > ago(60d) | count") == []


def test_retention_scoped_to_power_bi_table():
    assert check_retention("ContainerLog | where TimeGenerated > ago(90d) | count") == []


# ── QueryOutline parser (24h) ─────────────────────────────────────────────────


def test_parse_outline_basic_flags():
    q = (
        "let cutoff = ago(1d);\n"
        "PowerBIDatasetsWorkspace\n"
        "| where TimeGenerated > ago(30d)\n"
        "| join (OtherTable) on Id\n"
        "| summarize c = count() by ExecutingUser\n"
        "| take 100"
    )
    o = parse_outline(q)
    assert isinstance(o, QueryOutline)
    assert o.sourceTable == "PowerBIDatasetsWorkspace"
    assert o.letBindings == ["cutoff"]
    assert o.hasTimeFilter is True
    assert o.hasRowLimit is True
    assert o.hasSummarize is True
    assert o.hasJoin is True
    assert o.estimatedDataSource == "log-analytics"
    ops = [(s.operator, s.line) for s in o.pipelineSteps]
    assert ("where", 3) in ops
    assert ("join", 4) in ops
    assert ("summarize", 5) in ops
    assert ("take", 6) in ops
    assert all(isinstance(s, PipelineStep) for s in o.pipelineSteps)


def test_parse_outline_source_table_skips_keywords():
    # union starts the query → sourceTable is None (it is a keyword, not a table).
    o = parse_outline("union Table1, Table2 | where TimeGenerated > ago(1h)")
    assert o.sourceTable is None


def test_parse_outline_estimated_data_source_app_insights():
    # timestamp (lowercase, case-sensitive) → app-insights
    o = parse_outline("SomeTable | where timestamp > ago(1h)")
    assert o.estimatedDataSource == "app-insights"
    # Faithful to the TS: workspace-based App* tables resolve their time column to
    # TimeGenerated (detectTimeColumn), so estimatedDataSource is "log-analytics", NOT
    # "app-insights". The sourceTable→app-insights branch is only reached when the query
    # literally uses lowercase `timestamp`.
    o2 = parse_outline("AppRequests | take 5")
    assert o2.estimatedDataSource == "log-analytics"


def test_parse_outline_no_time_filter_and_unknown():
    o = parse_outline("CustomThing | project a, b")
    assert o.hasTimeFilter is False
    assert o.hasRowLimit is False
    assert o.estimatedDataSource == "unknown"


# ── Scoring & grading (26r) ───────────────────────────────────────────────────


def test_audit_perfect_query_scores_100_grade_a():
    q = "PowerBIDatasetsWorkspace | where TimeGenerated > ago(30d) | summarize count() by bin(TimeGenerated, 1h)"
    result = audit_kql(q)
    assert result["findings"] == []
    assert result["score"] == 100
    assert result["grade"] == "A"
    assert result["blocking"] is False


def test_audit_single_error_scores_75_grade_b():
    # CORRECT001 alone (error, -25) on a non-high-volume, time-filtered, limited query.
    q = "SomeTable | where TimeGenerated > ago(1h) | where col == null | take 10"
    result = audit_kql(q)
    assert _rule_ids(result["findings"]) == ["CORRECT001"]
    assert result["score"] == 75
    assert result["grade"] == "B"
    assert result["blocking"] is True


def test_audit_score_clamped_at_zero():
    # Many errors stack past 100 in deductions; score must clamp to 0 → grade F.
    q = (
        "PowerBIDatasetsWorkspace\n"
        "| where EventText contains \"Invoice Quantity\"\n"   # CORRECT007 (error) + PERF001 (warn)
        "| where a == null\n"                                  # CORRECT001 (error)
        "| where b == null\n"                                  # CORRECT001 (error)
        "| where c == null\n"                                  # CORRECT001 (error)
        "| where d == null"                                    # CORRECT001 (error)
    )
    result = audit_kql(q)
    assert result["score"] == 0
    assert result["grade"] == "F"
    assert result["blocking"] is True


# ── preflight_limits (24f) ────────────────────────────────────────────────────


def _checks_by_level(risks, level):
    return [r for r in risks if r["level"] == level]


def test_preflight_no_take_no_time_filter_is_high():
    risks = preflight_limits("ContainerLog | where Level == 'Error'")
    checks = {r["check"] for r in risks}
    assert "Result Truncation" in checks   # no take/summarize → HIGH
    assert "Timeout" in checks             # no time filter → HIGH
    assert "Data Size" in checks           # no project/summarize → MEDIUM
    # sorted HIGH first
    assert risks[0]["level"] == "HIGH"


def test_preflight_join_without_time_filter_is_high():
    risks = preflight_limits("A | join (B) on Id | take 10")
    checks = {r["check"] for r in risks}
    assert "Memory (E_RUNAWAY_QUERY)" in checks
    mem = [r for r in risks if r["check"] == "Memory (E_RUNAWAY_QUERY)"][0]
    assert mem["level"] == "HIGH"


def test_preflight_expected_rows_over_400k_is_high():
    q = "ContainerLog | where TimeGenerated > ago(1h) | take 500000"
    risks = preflight_limits(q, expected_row_count=450_000)
    trunc = [r for r in risks if r["check"] == "Result Truncation"]
    assert trunc and trunc[0]["level"] == "HIGH"
    assert "450,000" in trunc[0]["message"]


def test_preflight_make_series_is_low():
    q = "ContainerLog | where TimeGenerated > ago(1h) | make-series c = count() default = 0 on TimeGenerated step 5m"
    risks = preflight_limits(q)
    ms = [r for r in risks if r["check"] == "Memory"]
    assert ms and ms[0]["level"] == "LOW"


def test_preflight_many_unions_and_lets_are_medium():
    unions = "union A, B\n" + "\n".join(f"| union T{i}" for i in range(6))
    risks = preflight_limits(unions + " | take 5")
    complexity = [r for r in risks if r["check"] == "Query Complexity"]
    assert any("union" in r["message"] for r in complexity)
    assert all(r["level"] == "MEDIUM" for r in complexity)

    lets = "".join(f"let v{i} = 1;\n" for i in range(11)) + "T | where TimeGenerated > ago(1h) | take 5"
    risks2 = preflight_limits(lets)
    assert any(r["check"] == "Query Complexity" and "let" in r["message"] for r in risks2)


def test_preflight_set_notruncation_without_take_is_high():
    q = "set notruncation;\nContainerLog | where TimeGenerated > ago(1h)"
    risks = preflight_limits(q)
    override = [r for r in risks if r["check"] == "Result Truncation Override"]
    assert override and override[0]["level"] == "HIGH"


def test_preflight_clean_query_has_no_risks():
    q = "ContainerLog | where TimeGenerated > ago(1h) | project a, b | summarize count() by a | take 100"
    assert preflight_limits(q) == []


# ── parse_kusto_error ─────────────────────────────────────────────────────────


def test_parse_kusto_error_result_set_too_large():
    out = parse_kusto_error("Request failed: E_QUERY_RESULT_SET_TOO_LARGE partial results")
    assert out["code"] == "E_QUERY_RESULT_SET_TOO_LARGE"
    assert any("take" in s for s in out["suggestions"])


def test_parse_kusto_error_runaway():
    out = parse_kusto_error("query exceeded memory: E_RUNAWAY_QUERY")
    assert out["code"] == "E_RUNAWAY_QUERY"
    assert any("shufflekey" in s for s in out["suggestions"])


def test_parse_kusto_error_timeout():
    assert parse_kusto_error("The request timed out after 4 minutes")["code"] == "TIMEOUT"
    assert parse_kusto_error("gateway timeout")["code"] == "TIMEOUT"
    assert parse_kusto_error("request took too long")["code"] == "TIMEOUT"


def test_parse_kusto_error_auth():
    assert parse_kusto_error("HTTP 401 Unauthorized")["code"] == "AUTH"
    assert parse_kusto_error("403 Forbidden")["code"] == "AUTH"
    out = parse_kusto_error("Unauthorized")
    assert any("az login" in s for s in out["suggestions"])


def test_parse_kusto_error_unknown_passthrough():
    out = parse_kusto_error("some unrelated failure")
    assert out["code"] == "UNKNOWN"
    assert out["suggestions"] == ["some unrelated failure"]
