"""Part 24a — tests for the domain-relevant rule port onto kql_audit_rules.py.

One focused test per newly-ported rule (PERF002, PERF004-011, CORRECT002-006, BEST001-006,
HINT001-002): a query that trips it produces the finding (right ruleId + severity), a clean
query does not, and audit_kql's grade degrades appropriately. Deterministic/offline — pure
string-in/dict-out, no engine or network calls.

TELEMETRY001-003 are deliberately NOT ported (out of domain — see the module docstring "PART
24a" section in kql_audit_rules.py); there is nothing to test here for them.
"""

from fabric_audit_agent.query.kql_audit_rules import (
    audit_kql,
    check_best001,
    check_best002,
    check_best003,
    check_best004,
    check_best005,
    check_best006,
    check_correct002,
    check_correct003,
    check_correct004,
    check_correct005,
    check_correct006,
    check_hint001,
    check_hint002,
    check_perf002,
    check_perf004,
    check_perf005,
    check_perf006,
    check_perf007,
    check_perf008,
    check_perf009,
    check_perf010,
    check_perf011,
)


def _rule_ids(findings):
    return [f["ruleId"] for f in findings]


def _fires(kql, rule_id):
    return any(f["ruleId"] == rule_id for f in audit_kql(kql)["findings"])


# ── PERF002: tolower()/toupper() in where ─────────────────────────────────────


def test_perf002_fires_on_tolower_in_where():
    findings = check_perf002('requests | where tolower(cloud_RoleName) == "myservice"')
    assert findings and findings[0]["ruleId"] == "PERF002"
    assert findings[0]["severity"] == "warning"


def test_perf002_does_not_fire_on_case_insensitive_operator():
    assert check_perf002('requests | where cloud_RoleName =~ "myservice"') == []


def test_perf002_does_not_fire_when_tolower_outside_where():
    # extend, not where — no filter-clause re-cast, so PERF002 does not apply.
    assert check_perf002('requests | extend lc = tolower(cloud_RoleName)') == []


# ── PERF004: order by without take/top ─────────────────────────────────────────


def test_perf004_fires_on_order_by_without_limit():
    findings = check_perf004("requests | order by duration desc")
    assert findings and findings[0]["ruleId"] == "PERF004"
    assert findings[0]["severity"] == "warning"


def test_perf004_does_not_fire_with_top():
    assert check_perf004("requests | top 10 by duration desc") == []


def test_perf004_does_not_fire_with_order_by_plus_take():
    assert check_perf004("requests | order by duration desc | take 10") == []


# ── PERF005: join without pre-filtering ─────────────────────────────────────────


def test_perf005_fires_on_unfiltered_join():
    q = "requests | join dependencies on operation_Id"
    findings = check_perf005(q)
    assert findings and findings[0]["ruleId"] == "PERF005"
    assert findings[0]["severity"] == "warning"


def test_perf005_does_not_fire_when_filtered_nearby():
    q = (
        "requests\n"
        "| where timestamp > ago(1h)\n"
        "| join kind=inner (\n"
        "    dependencies\n"
        "    | where timestamp > ago(1h)\n"
        ") on operation_Id"
    )
    assert check_perf005(q) == []


# ── PERF006: bare count() on a high-volume table ────────────────────────────────


def test_perf006_fires_on_bare_count_high_volume_table():
    findings = check_perf006("ContainerLog | count")
    assert findings and findings[0]["ruleId"] == "PERF006"
    assert findings[0]["severity"] == "info"


def test_perf006_does_not_fire_with_summarize():
    assert check_perf006("ContainerLog | summarize count()") == []


def test_perf006_scoped_to_high_volume_tables():
    assert check_perf006("SomeCustomTable_CL | count") == []


# ── PERF007: repeated let without materialize() ─────────────────────────────────


def test_perf007_fires_on_repeated_let_without_materialize():
    q = (
        "let base = SomeTable | where TimeGenerated > ago(1d);\n"
        "base | where a == 1\n"
        "| join (base | where a == 2) on Id"
    )
    findings = check_perf007(q)
    assert findings and findings[0]["ruleId"] == "PERF007"
    assert findings[0]["severity"] == "warning"


def test_perf007_does_not_fire_when_materialized():
    q = (
        "let base = materialize(SomeTable | where TimeGenerated > ago(1d));\n"
        "base | where a == 1\n"
        "| join (base | where a == 2) on Id"
    )
    assert check_perf007(q) == []


def test_perf007_does_not_fire_on_single_use():
    q = "let base = SomeTable | where TimeGenerated > ago(1d);\nbase | take 10"
    assert check_perf007(q) == []


# ── PERF008: union * ─────────────────────────────────────────────────────────────


def test_perf008_fires_on_union_star():
    findings = check_perf008("union * | where TimeGenerated > ago(1h)")
    assert findings and findings[0]["ruleId"] == "PERF008"
    assert findings[0]["severity"] == "warning"


def test_perf008_does_not_fire_on_named_union():
    assert check_perf008("union Table1, Table2 | take 5") == []


def test_perf008_exempts_table_discovery_idiom():
    q = "union withsource=T * | where TimeGenerated > ago(1h) | distinct T"
    assert check_perf008(q) == []


# ── PERF009: filter on a calculated (extend) column ──────────────────────────────


def test_perf009_fires_on_filter_after_extend():
    q = "T | extend risk = a + b\n| where risk > 10"
    findings = check_perf009(q)
    assert findings and findings[0]["ruleId"] == "PERF009"
    assert findings[0]["severity"] == "info"


def test_perf009_does_not_fire_when_filter_on_original_column():
    q = "T | extend risk = a + b\n| where a > 10"
    assert check_perf009(q) == []


# ── PERF010: high-cardinality summarize without shufflekey hint ─────────────────


def test_perf010_fires_on_high_cardinality_summarize():
    findings = check_perf010("T | summarize count() by user_id")
    assert findings and findings[0]["ruleId"] == "PERF010"
    assert findings[0]["severity"] == "info"


def test_perf010_does_not_fire_with_shufflekey_hint():
    assert check_perf010("T | summarize hint.shufflekey=user_id count() by user_id") == []


def test_perf010_does_not_fire_on_low_cardinality_group():
    assert check_perf010("T | summarize count() by bin(TimeGenerated, 1h)") == []


# ── PERF011 (new — dcount vs count(distinct)) ────────────────────────────────────


def test_perf011_fires_on_count_distinct():
    findings = check_perf011("requests | summarize count(distinct user_Id)")
    assert findings and findings[0]["ruleId"] == "PERF011"
    assert findings[0]["severity"] == "info"


def test_perf011_does_not_fire_on_dcount():
    assert check_perf011("requests | summarize dcount(user_Id)") == []


# ── CORRECT002: summarize by datetime without bin() ──────────────────────────────


def test_correct002_fires_on_raw_datetime_group():
    findings = check_correct002("T | summarize count() by TimeGenerated")
    assert findings and findings[0]["ruleId"] == "CORRECT002"
    assert findings[0]["severity"] == "warning"


def test_correct002_does_not_fire_with_bin():
    assert check_correct002("T | summarize count() by bin(TimeGenerated, 1h)") == []


# ── CORRECT003: SQL syntax ────────────────────────────────────────────────────────


def test_correct003_fires_on_select_and_group_by():
    findings = check_correct003("SELECT col1, col2 FROM T GROUP BY col1")
    ids = _rule_ids(findings)
    assert "CORRECT003" in ids
    assert all(f["severity"] == "error" for f in findings)


def test_correct003_does_not_fire_on_valid_kql():
    assert check_correct003("T | project col1, col2 | summarize count() by col1") == []


def test_correct003_inner_join_fires_separately_from_select_from():
    # INNER JOIN alone (no SELECT/FROM/GROUP BY on the line) still fires CORRECT003.
    findings = check_correct003("T | where x == 1 -- pretend INNER JOIN Other ON T.Id = Other.Id")
    assert any(f["ruleId"] == "CORRECT003" for f in findings)


# BUG 3: SQL-syntax tokens INSIDE a quoted string literal are normal KQL, not SQL — they must
# not fire CORRECT003 (an "error"/blocking finding), while real SQL-shaped code still does.

def test_correct003_does_not_fire_on_sql_keywords_inside_string_literal():
    assert check_correct003('AppRequests | where Url contains "select * from orders"') == []
    assert check_correct003("T | where Message has 'GROUP BY region'") == []


def test_correct003_still_fires_on_real_sql_syntax():
    findings = check_correct003("SELECT * FROM T")
    assert any(f["ruleId"] == "CORRECT003" for f in findings)
    assert all(f["severity"] == "error" for f in findings)


# ── CORRECT004: deprecated mvexpand ────────────────────────────────────────────────


def test_correct004_fires_on_mvexpand():
    findings = check_correct004("T | mvexpand col")
    assert findings and findings[0]["ruleId"] == "CORRECT004"
    assert findings[0]["severity"] == "warning"


def test_correct004_does_not_fire_on_mv_expand():
    assert check_correct004("T | mv-expand col") == []


# ── CORRECT005: wrong time column on App Insights tables ────────────────────────


def test_correct005_fires_on_timestamp_against_app_table():
    findings = check_correct005("AppRequests | where timestamp > ago(1h)")
    assert findings and findings[0]["ruleId"] == "CORRECT005"
    assert findings[0]["severity"] == "error"


def test_correct005_does_not_fire_with_time_generated():
    assert check_correct005("AppRequests | where TimeGenerated > ago(1h)") == []


def test_correct005_scoped_to_app_insights_tables():
    assert check_correct005("ContainerLog | where timestamp > ago(1h)") == []


# BUG 3: a `timestamp` token inside a quoted string literal must not fire CORRECT005.

def test_correct005_does_not_fire_on_timestamp_inside_string_literal():
    assert check_correct005('AppRequests | where Url contains "timestamp"') == []


def test_correct005_still_fires_on_real_timestamp_column_reference():
    findings = check_correct005('AppRequests | where timestamp > ago(1h) and Url contains "x"')
    assert findings and findings[0]["ruleId"] == "CORRECT005"


# ── CORRECT006: Unix-timestamp conversion in query ───────────────────────────────


def test_correct006_fires_on_unix_conversion():
    findings = check_correct006("T | extend t = unixtime_seconds_todatetime(epoch)")
    assert findings and findings[0]["ruleId"] == "CORRECT006"
    assert findings[0]["severity"] == "info"


def test_correct006_does_not_fire_without_conversion():
    assert check_correct006("T | where TimeGenerated > ago(1h)") == []


# ── BEST001: no time filter ───────────────────────────────────────────────────────


def test_best001_fires_without_time_filter():
    findings = check_best001("T | take 10")
    assert findings and findings[0]["ruleId"] == "BEST001"
    assert findings[0]["severity"] == "info"
    assert "where TimeGenerated > ago(24h)" in findings[0]["corrected"]


def test_best001_does_not_fire_with_time_filter():
    assert check_best001("T | where TimeGenerated > ago(1h) | take 10") == []


# ── BEST002: no row limit ─────────────────────────────────────────────────────────


def test_best002_fires_without_row_limit():
    findings = check_best002("T | where TimeGenerated > ago(1h)")
    assert findings and findings[0]["ruleId"] == "BEST002"
    assert findings[0]["severity"] == "info"
    assert "take 1000" in findings[0]["corrected"]


def test_best002_does_not_fire_with_take():
    assert check_best002("T | where TimeGenerated > ago(1h) | take 10") == []


def test_best002_does_not_fire_with_summarize_or_count():
    assert check_best002("T | summarize count()") == []
    assert check_best002("T | count") == []


# ── BEST003: render without summarize ─────────────────────────────────────────────


def test_best003_fires_on_render_without_summarize():
    findings = check_best003("T | where TimeGenerated > ago(1h) | render timechart")
    assert findings and findings[0]["ruleId"] == "BEST003"
    assert findings[0]["severity"] == "info"


def test_best003_does_not_fire_with_summarize_before_render():
    q = "T | summarize count() by bin(TimeGenerated, 1h) | render timechart"
    assert check_best003(q) == []


# ── BEST004: filter after summarize ───────────────────────────────────────────────


def test_best004_fires_on_where_after_summarize():
    q = "T | summarize c = count() by a\n| where c > 10"
    findings = check_best004(q)
    assert findings and findings[0]["ruleId"] == "BEST004"
    assert findings[0]["severity"] == "info"


def test_best004_does_not_fire_when_filter_before_summarize():
    q = "T | where a > 10\n| summarize c = count() by a"
    assert check_best004(q) == []


# ── BEST005: missing bin() on time-series tables ─────────────────────────────────


def test_best005_fires_on_time_series_table_summarize_without_bin():
    findings = check_best005("Perf | summarize avg(CounterValue) by Computer")
    assert findings and findings[0]["ruleId"] == "BEST005"
    assert findings[0]["severity"] == "warning"


def test_best005_does_not_fire_with_bin():
    q = "Perf | summarize avg(CounterValue) by bin(TimeGenerated, 5m)"
    assert check_best005(q) == []


def test_best005_scoped_to_time_series_tables():
    assert check_best005("SomeOtherTable | summarize count() by Computer") == []


# ── BEST006: set notruncation without row limit ──────────────────────────────────


def test_best006_fires_on_notruncation_without_limit():
    q = "set notruncation;\nT | where TimeGenerated > ago(1h)"
    findings = check_best006(q)
    assert findings and findings[0]["ruleId"] == "BEST006"
    assert findings[0]["severity"] == "warning"


def test_best006_does_not_fire_when_paired_with_take():
    q = "set notruncation;\nT | where TimeGenerated > ago(1h) | take 1000"
    assert check_best006(q) == []


# ── HINT001: no column selection on a high-volume table ─────────────────────────


def test_hint001_fires_without_project_or_summarize():
    findings = check_hint001("ContainerLog | where TimeGenerated > ago(1h)")
    assert findings and findings[0]["ruleId"] == "HINT001"
    assert findings[0]["severity"] == "hint"


def test_hint001_does_not_fire_with_project():
    assert check_hint001("ContainerLog | where TimeGenerated > ago(1h) | project a, b") == []


def test_hint001_is_zero_score_deduction():
    # `take 10` satisfies BEST002's row-limit check and the time filter satisfies BEST001, so
    # HINT001 (no project/summarize on a high-volume table) is the ONLY finding — isolating its
    # zero-deduction contract.
    q = "ContainerLog | where TimeGenerated > ago(1h) | take 10"
    result = audit_kql(q)
    assert _rule_ids(result["findings"]) == ["HINT001"]
    assert result["score"] == 100
    assert result["grade"] == "A"


# ── HINT002: count without time filter on a time-series table ───────────────────


def test_hint002_fires_on_count_without_time_filter():
    findings = check_hint002("Perf | count")
    assert findings and findings[0]["ruleId"] == "HINT002"
    assert findings[0]["severity"] == "hint"


def test_hint002_does_not_fire_with_time_filter():
    assert check_hint002("Perf | where TimeGenerated > ago(1h) | count") == []


def test_hint002_scoped_to_time_series_tables():
    assert check_hint002("SomeOtherTable | count") == []


# ── Grade degradation through audit_kql (integration) ─────────────────────────────


def test_audit_kql_degrades_grade_for_new_warning_rules():
    # PERF004 (warning, -10) + PERF005 (warning, -10) stack; no time filter/row limit also fires
    # BEST001/BEST002 (info, -2 each) -> total -24 -> score 76 -> grade B (>= 75).
    q = "T | join Other on Id\n| order by x desc"
    result = audit_kql(q)
    ids = set(_rule_ids(result["findings"]))
    assert {"PERF004", "PERF005", "BEST001", "BEST002"} <= ids
    assert result["score"] == 100 - 10 - 10 - 2 - 2
    assert result["grade"] == "B"


def test_audit_kql_degrades_grade_for_new_error_rule_correct003():
    q = "SELECT col FROM T"
    result = audit_kql(q)
    assert "CORRECT003" in _rule_ids(result["findings"])
    assert result["blocking"] is True
    assert result["grade"] in ("D", "F")


def test_audit_kql_hint_rules_never_move_the_score():
    # AppRequests is a TIME_SERIES table but NOT a HIGH_VOLUME one, so `count` alone (no time
    # filter) trips exactly two findings: BEST001 (info, -2 — no time filter) and HINT002 (hint,
    # 0 — count without a time filter on a time-series table). The score reflects only BEST001's
    # deduction, confirming HINT002 contributes nothing even when routed through audit_kql.
    q = "AppRequests | count"
    result = audit_kql(q)
    ids = _rule_ids(result["findings"])
    assert set(ids) == {"BEST001", "HINT002"}
    assert result["score"] == 98
    assert result["grade"] == "A"
    assert result["blocking"] is False
