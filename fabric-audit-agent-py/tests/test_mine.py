"""Pure foundation for the query-library growth loop: the [adhoc-kql] audit-log parser and the
shape canonicalizer. No I/O, no ranking, no CLI yet (later tasks)."""
import json

from fabric_audit_agent.query.mine import parse_audit_lines, shape_key


def _line(engine="capacity", verdict="allowed", kql="CapacityEvents | take 50", **extra):
    rec = {"tag": "adhoc-kql", "engine": engine, "verdict": verdict}
    rec.update(extra)
    if kql is not None:
        rec["kql"] = kql
    return "[adhoc-kql] " + json.dumps(rec, ensure_ascii=False, separators=(",", ": "))


# ---------------------------------------------------------------------------
# parse_audit_lines
# ---------------------------------------------------------------------------

def test_parse_extracts_allowed_capacity_record():
    line = _line(
        engine="capacity", verdict="allowed", rowCount=12,
        kql="CapacityEvents\n| where ingestion_time() > ago(1d)\n| take 50",
    )
    recs = parse_audit_lines([line])
    assert len(recs) == 1
    assert recs[0]["engine"] == "capacity"
    assert recs[0]["verdict"] == "allowed"
    assert recs[0]["rowCount"] == 12


def test_parse_extracts_allowed_la_record():
    line = _line(
        engine="la", verdict="allowed",
        kql="PowerBIDatasetsWorkspace\n| where TimeGenerated > ago(1d)\n| take 20",
    )
    recs = parse_audit_lines([line])
    assert len(recs) == 1
    assert recs[0]["engine"] == "la"


def test_parse_skips_rejected_records():
    line = _line(
        engine="capacity", verdict="rejected", stage="denied-operator", reason="denied op",
        kql="CapacityEvents | union database('x').T",
    )
    assert parse_audit_lines([line]) == []


def test_parse_skips_non_marker_lines_without_raising():
    lines = ["just a regular log line", "another line with no marker at all", ""]
    assert parse_audit_lines(lines) == []


def test_parse_skips_malformed_json_without_raising():
    lines = [
        "[adhoc-kql] {this is not valid json",
        "[adhoc-kql] ",
        "[adhoc-kql] [1, 2, 3]",   # valid json, but not a dict -- must be skipped, not raise
        "[adhoc-kql] 42",          # valid json scalar -- must be skipped, not raise
    ]
    assert parse_audit_lines(lines) == []


def test_parse_drops_unknown_engine():
    line = _line(engine="sql", verdict="allowed", kql="SELECT 1")
    assert parse_audit_lines([line]) == []


def test_parse_extracts_line_with_logger_prefix_before_marker():
    raw = _line(engine="capacity", verdict="allowed", kql="CapacityEvents\n| take 50")
    line = "2026-07-08 12:00:00 INFO app.audit " + raw
    recs = parse_audit_lines([line])
    assert len(recs) == 1
    assert recs[0]["engine"] == "capacity"


def test_parse_accepts_any_iterable_of_strings_not_just_a_list():
    line = _line(engine="capacity", verdict="allowed", kql="CapacityEvents\n| take 50")

    def gen():
        yield line
        yield "not a marker line"

    recs = parse_audit_lines(gen())
    assert len(recs) == 1


def test_parse_returns_multiple_allowed_records_in_order():
    l1 = _line(engine="capacity", verdict="allowed", kql="CapacityEvents\n| take 50")
    l2 = _line(engine="la", verdict="allowed", kql="PowerBIDatasetsWorkspace\n| take 20")
    recs = parse_audit_lines([l1, "junk", l2])
    assert [r["engine"] for r in recs] == ["capacity", "la"]


# ---------------------------------------------------------------------------
# shape_key -- same-key cases
# ---------------------------------------------------------------------------

_PEAK_WINDOWS_24H = (
    "CapacityEvents\n| where ingestion_time() > ago(1d)\n"
    "| extend base = tolong(data.baseCapacityUnits), used = tolong(data.capacityUnitMs), "
    "win = tostring(data.windowStartTime)\n| where base > 0\n"
    "| summarize cuPct = max(100.0 * used / (base * 1000 * 30)) by win\n"
    "| top 50 by cuPct desc"
)

_PEAK_WINDOWS_7D = (
    "CapacityEvents\n| where ingestion_time() > ago(7d)\n"
    "| extend base = tolong(data.baseCapacityUnits), used = tolong(data.capacityUnitMs), "
    "win = tostring(data.windowStartTime)\n| where base > 0\n"
    "| summarize cuPct = max(100.0 * used / (base * 1000 * 30)) by win\n"
    "| top 50 by cuPct desc"
)


def test_shape_key_ago_window_difference_is_same_shape_on_real_library_queries():
    # Real templates from query_library.json: identical shape, differ only in the ago() window.
    assert shape_key(_PEAK_WINDOWS_24H) == shape_key(_PEAK_WINDOWS_7D)


def test_shape_key_date_threshold_whitespace_and_operator_case_are_same_shape():
    variant = (
        "CapacityEvents\n| WHERE ingestion_time()   >   ago(24h)\n"
        "| extend base = tolong(data.baseCapacityUnits), used = tolong(data.capacityUnitMs), "
        "win = tostring(data.windowStartTime)\n| where base > 5\n"
        "| summarize cuPct = max(100.0 * used / (base * 1000 * 30)) by win\n"
        "| TOP 50 BY cuPct DESC"
    )
    assert shape_key(_PEAK_WINDOWS_24H) == shape_key(variant)


def test_shape_key_take_count_does_not_affect_shape():
    base = "CapacityEvents | where ingestion_time() > ago(1d) | project win"
    assert shape_key(base + "\n| take 100") == shape_key(base + "\n| take 500")


def test_shape_key_limit_and_take_are_the_same_shape():
    base = "CapacityEvents | where ingestion_time() > ago(1d) | project win"
    assert shape_key(base + "\n| limit 100") == shape_key(base + "\n| take 100")


def test_shape_key_doubled_trailing_bound_matches_single_take_twin():
    base = "CapacityEvents | where ingestion_time() > ago(1d) | project win"
    # Agent's own query already ended in "| take 50"; the audit line wraps it again in "| take 100"
    # (tools.py:1568 always appends its own bound) -- a doubled trailing bound.
    agent_query_already_bounded = base + "\n| take 50"
    doubled = agent_query_already_bounded + "\n| take 100"
    single_take_twin = base + "\n| take 100"
    assert shape_key(doubled) == shape_key(single_take_twin) == shape_key(base)


def test_shape_key_ago_1d_and_ago_24h_are_the_same_shape():
    assert shape_key("T | where x > ago(1d)") == shape_key("T | where x > ago(24h)")


# ---------------------------------------------------------------------------
# shape_key -- different-key cases
# ---------------------------------------------------------------------------

def test_shape_key_bin_hourly_vs_bin_daily_are_different_shapes():
    hourly = "CapacityEvents | summarize avg(x) by bin(win, 1h)"
    daily = "CapacityEvents | summarize avg(x) by bin(win, 1d)"
    assert shape_key(hourly) != shape_key(daily)


def test_shape_key_ago_is_normalized_but_bin_is_preserved():
    q_ago1d_bin1h = "CapacityEvents | where ingestion_time() > ago(1d) | summarize avg(x) by bin(win, 1h)"
    q_ago24h_bin1h = "CapacityEvents | where ingestion_time() > ago(24h) | summarize avg(x) by bin(win, 1h)"
    q_ago1d_bin1d = "CapacityEvents | where ingestion_time() > ago(1d) | summarize avg(x) by bin(win, 1d)"
    # ago() difference alone -> same shape (bin unchanged).
    assert shape_key(q_ago1d_bin1h) == shape_key(q_ago24h_bin1h)
    # bin() difference -> different shape, even though ago() is normalized in both.
    assert shape_key(q_ago1d_bin1h) != shape_key(q_ago1d_bin1d)


def test_shape_key_formula_constant_difference_is_a_different_shape():
    # Honesty guard: a wrong CU-formula constant must never merge with the correct one.
    correct = (
        "CapacityEvents | extend cuPct = 100.0 * used / (base * 1000 * 30) | where cuPct > 80"
    )
    wrong = (
        "CapacityEvents | extend cuPct = 100.0 * used / (base * 1000 * 60) | where cuPct > 80"
    )
    assert shape_key(correct) != shape_key(wrong)


def test_shape_key_top_n_is_pinned_as_a_different_shape():
    # Documented behavior: top N is never placeholdered, so different N -> different shape.
    top50 = "CapacityEvents | project win | top 50 by win desc"
    top20 = "CapacityEvents | project win | top 20 by win desc"
    assert shape_key(top50) != shape_key(top20)


def test_shape_key_genuinely_different_queries_are_different_shapes():
    trend = (
        "CapacityEvents\n| where ingestion_time() > ago(1d)\n"
        "| extend base = tolong(data.baseCapacityUnits), used = tolong(data.capacityUnitMs), "
        "win = todatetime(data.windowStartTime)\n| where base > 0\n"
        "| summarize avgCuPct = avg(100.0 * used / (base * 1000 * 30)) by bin(win, 1h)\n"
        "| sort by win asc"
    )
    la_query = (
        "PowerBIDatasetsWorkspace\n| where TimeGenerated > ago(1d)\n"
        "| where isnotempty(ExecutingUser)\n"
        "| summarize totalCpuMs = sum(CpuTimeMs), ops = count() by ExecutingUser\n"
        "| top 20 by totalCpuMs desc"
    )
    assert shape_key(_PEAK_WINDOWS_24H) != shape_key(trend)
    assert shape_key(trend) != shape_key(la_query)


def test_shape_key_is_pure_and_deterministic():
    assert shape_key(_PEAK_WINDOWS_24H) == shape_key(_PEAK_WINDOWS_24H)
    assert isinstance(shape_key(_PEAK_WINDOWS_24H), str)
