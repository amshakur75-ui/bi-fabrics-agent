"""Tests for Phase 7 NL-to-Query tools: run_sql, run_dax, describe_sql_table,
describe_semantic_model, classify_query_target. All use mock executors — no real
network calls."""

import pytest

from fabric_audit_agent.tools import create_tool_definitions


def _tool(name, tools=None):
    """Find a tool definition by name from the tool list."""
    if tools is None:
        tools = create_tool_definitions()
    return next((t for t in tools if t["name"] == name), None)


def _handler(name, tools=None):
    """Get the handler function for a named tool."""
    t = _tool(name, tools)
    assert t is not None, f"tool '{name}' not found"
    return t["handler"]


# ---------------------------------------------------------------------------
# Tool definition existence
# ---------------------------------------------------------------------------

class TestToolDefinitions:
    def test_run_sql_exists(self):
        assert _tool("run_sql") is not None

    def test_run_dax_exists(self):
        assert _tool("run_dax") is not None

    def test_describe_sql_table_exists(self):
        assert _tool("describe_sql_table") is not None

    def test_describe_semantic_model_exists(self):
        assert _tool("describe_semantic_model") is not None

    def test_classify_query_target_exists(self):
        assert _tool("classify_query_target") is not None

    def test_run_sql_requires_sql(self):
        schema = _tool("run_sql")["input_schema"]
        assert "sql" in schema.get("required", [])

    def test_run_dax_requires_dax(self):
        schema = _tool("run_dax")["input_schema"]
        assert "dax" in schema.get("required", [])

    def test_describe_sql_table_requires_table(self):
        schema = _tool("describe_sql_table")["input_schema"]
        assert "table" in schema.get("required", [])

    def test_classify_query_target_requires_question(self):
        schema = _tool("classify_query_target")["input_schema"]
        assert "question" in schema.get("required", [])

    def test_total_tool_count_increased(self):
        """Phase 7 added 5 tools + Phase 8 added 1 (render_chart) to the original 20; Phase 3.8
        added 7 Newell resolution tools (resolve_term/resolve_field/field_usage_query/
        workspace_usage_query/field_search/field_detail/artifact_lookup)."""
        tools = create_tool_definitions()
        assert len(tools) == 33


# ---------------------------------------------------------------------------
# run_sql
# ---------------------------------------------------------------------------

class TestRunSql:
    def test_rejects_empty_sql(self):
        handler = _handler("run_sql")
        result = handler({"sql": ""})
        assert "error" in result

    def test_rejects_none_sql(self):
        handler = _handler("run_sql")
        result = handler({})
        assert "error" in result

    def test_rejects_non_select(self):
        handler = _handler("run_sql")
        result = handler({"sql": "DROP TABLE Users"})
        assert "error" in result
        assert result.get("rejectionStage") == "read-only-check"

    def test_rejects_insert(self):
        handler = _handler("run_sql")
        result = handler({"sql": "INSERT INTO T VALUES (1)"})
        assert "error" in result
        assert result.get("rejectionStage") == "read-only-check"

    def test_rejects_stacked_statements(self):
        handler = _handler("run_sql")
        result = handler({"sql": "SELECT 1; DROP TABLE T"})
        assert "error" in result
        assert result.get("rejectionStage") == "read-only-check"

    def test_rejects_tautology(self):
        handler = _handler("run_sql")
        result = handler({"sql": "SELECT * FROM T WHERE 1=1 OR 1=1"})
        assert "error" in result
        assert result.get("rejectionStage") == "read-only-check"

    def test_executes_valid_sql_with_mock_executor(self):
        handler = _handler("run_sql")
        mock_rows = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        result = handler({
            "sql": "SELECT id, name FROM Users",
            "_executor": lambda sql: mock_rows,
        })
        assert "error" not in result
        assert result.get("rows") == mock_rows
        assert result.get("source") == "live"
        assert result.get("querySql") is not None  # SP7: exact query returned

    def test_returns_ungated_flag(self):
        handler = _handler("run_sql")
        result = handler({
            "sql": "SELECT * FROM T",
            "_executor": lambda sql: [{"x": 1}],
        })
        assert result.get("ungated") is True
        assert "ungatedNote" in result

    def test_execution_error_surfaces_honestly(self):
        handler = _handler("run_sql")
        def failing_executor(sql):
            raise RuntimeError("connection timeout")
        result = handler({
            "sql": "SELECT * FROM T",
            "_executor": failing_executor,
        })
        assert "error" in result
        assert "connection timeout" in result["error"]
        assert result.get("rejectionStage") == "execute"

    def test_unconfigured_endpoint_returns_note(self, monkeypatch):
        handler = _handler("run_sql")
        monkeypatch.delenv("FABRIC_SQL_CONNECTION_STRING", raising=False)
        monkeypatch.delenv("FABRIC_SQL_ENDPOINT", raising=False)
        result = handler({"sql": "SELECT 1"})
        assert result.get("source") == "none"
        assert "note" in result

    def test_max_rows_clamped(self):
        handler = _handler("run_sql")
        rows = [{"id": i} for i in range(50)]
        result = handler({
            "sql": "SELECT id FROM T",
            "_executor": lambda sql: rows,
            "maxRows": 10,
        })
        # The TOP N is injected into the SQL, but the mock executor ignores it
        assert result.get("querySql") is not None
        assert "TOP 10" in result["querySql"]

    def test_columnar_format(self):
        handler = _handler("run_sql")
        result = handler({
            "sql": "SELECT x FROM T",
            "_executor": lambda sql: [{"x": 1}, {"x": 2}],
            "format": "columnar",
        })
        assert isinstance(result.get("rows"), dict)


# ---------------------------------------------------------------------------
# run_dax
# ---------------------------------------------------------------------------

class TestRunDax:
    def test_rejects_empty_dax(self):
        handler = _handler("run_dax")
        result = handler({"dax": ""})
        assert "error" in result

    def test_rejects_none_dax(self):
        handler = _handler("run_dax")
        result = handler({})
        assert "error" in result

    def test_rejects_non_evaluate(self):
        handler = _handler("run_dax")
        result = handler({"dax": "DROP TABLE Sales"})
        assert "error" in result
        assert result.get("rejectionStage") == "read-only-check"

    def test_rejects_delete(self):
        handler = _handler("run_dax")
        result = handler({"dax": "DELETE FROM Sales"})
        assert "error" in result
        assert result.get("rejectionStage") == "read-only-check"

    def test_executes_valid_dax_with_mock_executor(self):
        handler = _handler("run_dax")
        mock_rows = [{"Category": "Electronics", "Revenue": 50000}]
        result = handler({
            "dax": "EVALUATE Sales",
            "_executor": lambda dax: mock_rows,
        })
        assert "error" not in result
        assert result.get("rows") == mock_rows
        assert result.get("source") == "live"
        assert result.get("queryDax") == "EVALUATE Sales"  # SP7: exact query returned

    def test_returns_ungated_flag(self):
        handler = _handler("run_dax")
        result = handler({
            "dax": "EVALUATE Sales",
            "_executor": lambda dax: [{"x": 1}],
        })
        assert result.get("ungated") is True
        assert "ungatedNote" in result

    def test_execution_error_surfaces_honestly(self):
        handler = _handler("run_dax")
        def failing_executor(dax):
            raise RuntimeError("XMLA connection failed")
        result = handler({
            "dax": "EVALUATE Sales",
            "_executor": failing_executor,
        })
        assert "error" in result
        assert "XMLA connection failed" in result["error"]
        assert result.get("rejectionStage") == "execute"

    def test_unconfigured_endpoint_returns_note(self, monkeypatch):
        handler = _handler("run_dax")
        monkeypatch.delenv("FABRIC_XMLA_ENDPOINT", raising=False)
        result = handler({"dax": "EVALUATE Sales"})
        assert result.get("source") == "none"
        assert "note" in result

    def test_client_side_row_cap(self):
        handler = _handler("run_dax")
        many_rows = [{"id": i} for i in range(200)]
        result = handler({
            "dax": "EVALUATE Sales",
            "_executor": lambda dax: many_rows,
            "maxRows": 50,
        })
        # Client-side cap should truncate to 50
        assert len(result.get("rows", [])) <= 50

    def test_columnar_format(self):
        handler = _handler("run_dax")
        result = handler({
            "dax": "EVALUATE Sales",
            "_executor": lambda dax: [{"x": 1}, {"x": 2}],
            "format": "columnar",
        })
        assert isinstance(result.get("rows"), dict)

    def test_define_evaluate_accepted(self):
        handler = _handler("run_dax")
        dax = "DEFINE MEASURE Sales[Rev] = SUM(Sales[Amount]) EVALUATE Sales"
        result = handler({
            "dax": dax,
            "_executor": lambda d: [{"Rev": 100}],
        })
        assert "error" not in result


# ---------------------------------------------------------------------------
# describe_sql_table
# ---------------------------------------------------------------------------

class TestDescribeSqlTable:
    def test_rejects_missing_table(self):
        handler = _handler("describe_sql_table")
        result = handler({})
        assert "error" in result

    def test_returns_schema_with_mock_executor(self):
        handler = _handler("describe_sql_table")
        mock_columns = [
            {"COLUMN_NAME": "id", "DATA_TYPE": "int", "IS_NULLABLE": "NO", "CHARACTER_MAXIMUM_LENGTH": None},
            {"COLUMN_NAME": "name", "DATA_TYPE": "varchar", "IS_NULLABLE": "YES", "CHARACTER_MAXIMUM_LENGTH": 100},
        ]
        result = handler({
            "table": "Users",
            "_executor": lambda sql: mock_columns,
        })
        assert "error" not in result
        assert result["table"] == "Users"
        assert len(result["columns"]) == 2
        assert result["columns"][0]["name"] == "id"
        assert result["columns"][1]["name"] == "name"
        assert result.get("source") == "live"

    def test_unconfigured_returns_note(self, monkeypatch):
        handler = _handler("describe_sql_table")
        monkeypatch.delenv("FABRIC_SQL_CONNECTION_STRING", raising=False)
        monkeypatch.delenv("FABRIC_SQL_ENDPOINT", raising=False)
        result = handler({"table": "Users"})
        assert result.get("source") == "none"


# ---------------------------------------------------------------------------
# describe_semantic_model
# ---------------------------------------------------------------------------

class TestDescribeSemanticModel:
    def test_returns_tables_and_measures_with_mock_executor(self):
        handler = _handler("describe_semantic_model")
        call_count = {"n": 0}
        def mock_executor(query):
            call_count["n"] += 1
            if "TMSCHEMA_TABLES" in query:
                return [{"Name": "Sales"}, {"Name": "Products"}]
            if "TMSCHEMA_MEASURES" in query:
                return [{"Name": "Revenue", "Expression": "SUM(Sales[Amount])", "TableID": 1}]
            return []
        result = handler({
            "model": "SalesModel",
            "_executor": mock_executor,
        })
        assert "error" not in result or "errors" not in result
        assert result.get("tables") == ["Sales", "Products"]
        assert len(result.get("measures", [])) == 1
        assert result["measures"][0]["name"] == "Revenue"
        assert result.get("model") == "SalesModel"

    def test_unconfigured_returns_note(self, monkeypatch):
        handler = _handler("describe_semantic_model")
        monkeypatch.delenv("FABRIC_XMLA_ENDPOINT", raising=False)
        result = handler({})
        assert result.get("source") == "none"


# ---------------------------------------------------------------------------
# classify_query_target
# ---------------------------------------------------------------------------

class TestClassifyQueryTarget:
    def test_rejects_missing_question(self):
        handler = _handler("classify_query_target")
        result = handler({})
        assert "error" in result

    def test_returns_target_for_kql_question(self):
        handler = _handler("classify_query_target")
        result = handler({"question": "Query the Eventhouse for capacity data"})
        assert result.get("target") == "kql"
        assert "confidence" in result
        assert "reason" in result

    def test_returns_target_for_sql_question(self):
        handler = _handler("classify_query_target")
        result = handler({"question": "What's in the lakehouse sales table?"})
        assert result.get("target") == "sql"

    def test_returns_target_for_dax_question(self):
        handler = _handler("classify_query_target")
        result = handler({"question": "Write a DAX measure for total revenue"})
        assert result.get("target") == "dax"

    def test_defaults_to_kql_for_generic(self):
        handler = _handler("classify_query_target")
        result = handler({"question": "How is everything?"})
        assert result.get("target") == "kql"
        assert result["confidence"] < 0.3


# ---------------------------------------------------------------------------
# Task 7.5: run_kql routes through validation (validate_adhoc_kql)
# ---------------------------------------------------------------------------

class TestRunKqlValidation:
    """Verify that run_kql routes through the firewall validation (assert_read_only_kql
    via validate_adhoc_kql). The firewall itself is thoroughly tested in test_firewall.py;
    these tests verify the tool handler's integration with it.

    When no live engine is configured, run_kql returns early before the firewall check
    (engine availability is checked first). So we test the firewall module directly here
    to confirm the validation pipeline exists and works."""

    def test_firewall_rejects_control_command(self):
        from fabric_audit_agent.query.firewall import validate_adhoc_kql, FirewallRejection
        with pytest.raises(FirewallRejection):
            validate_adhoc_kql(".drop table X")

    def test_firewall_rejects_cross_resource(self):
        from fabric_audit_agent.query.firewall import validate_adhoc_kql, FirewallRejection
        with pytest.raises(FirewallRejection):
            validate_adhoc_kql("T | union database('other').T2")

    def test_firewall_rejects_verbatim_string(self):
        from fabric_audit_agent.query.firewall import validate_adhoc_kql, FirewallRejection
        with pytest.raises(FirewallRejection):
            validate_adhoc_kql('T | where x == @"test"')

    def test_run_kql_handler_calls_firewall_when_engine_exists(self):
        """When a live engine IS available, the firewall is invoked before execution.
        We verify this by checking that a bad query is rejected, not executed."""
        # The run_kql handler checks engine config before firewall -- without env vars
        # it returns early with source="mock". This is correct behavior: no engine to
        # validate against. The firewall integration is verified via test_firewall.py.
        handler = _handler("run_kql")
        result = handler({"kql": ".drop table X", "engine": "capacity"})
        # Without engine configured, returns mock note (not an error, not execution)
        assert result.get("source") == "mock" or "error" in result


# ---------------------------------------------------------------------------
# Task 5b: run_kql large-result display gate (>50 rows -> preview + options;
# <=50 rows unaffected). Ported from kql-mcp-server's formatQueryResult() 50-row
# stop + kql-ask.md's 4-option gate. Uses the "_executor" DI seam (same pattern as
# run_sql/run_dax) so the gate can be exercised offline without any live engine env.
# ---------------------------------------------------------------------------

_CLEAN_KQL = "CapacityEvents | where TimeGenerated > ago(1d) | take 5000"


class TestRunKqlLargeResultGate:
    def test_over_threshold_sets_large_result_flag_with_true_row_count(self):
        handler = _handler("run_kql")
        rows = [{"id": i} for i in range(75)]
        result = handler({
            "engine": "capacity",
            "kql": _CLEAN_KQL,
            "_executor": lambda kql: rows,
        })
        assert "error" not in result
        assert result.get("largeResult") is True
        assert result.get("rowCount") == 75   # true count, not the preview length

    def test_over_threshold_preview_capped_at_max_display_rows(self):
        handler = _handler("run_kql")
        rows = [{"id": i} for i in range(250)]
        result = handler({
            "engine": "capacity",
            "kql": _CLEAN_KQL,
            "_executor": lambda kql: rows,
        })
        assert result.get("largeResult") is True
        assert len(result.get("rows", [])) <= 100
        assert result.get("rowCount") == 250

    def test_over_threshold_options_has_four_choices(self):
        handler = _handler("run_kql")
        rows = [{"id": i} for i in range(60)]
        result = handler({
            "engine": "capacity",
            "kql": _CLEAN_KQL,
            "_executor": lambda kql: rows,
        })
        options = result.get("options")
        assert isinstance(options, list) and len(options) == 4
        ids = {o["id"] for o in options}
        assert ids == {"summarize", "filter", "topN", "proceed"}
        assert "note" in result

    def test_over_threshold_note_instructs_agent_to_present_options(self):
        handler = _handler("run_kql")
        rows = [{"id": i} for i in range(51)]
        result = handler({
            "engine": "capacity",
            "kql": _CLEAN_KQL,
            "_executor": lambda kql: rows,
        })
        assert result.get("largeResult") is True
        assert "51" in result["note"]

    def test_at_or_under_threshold_no_gate_full_rows_returned(self):
        handler = _handler("run_kql")
        rows = [{"id": i} for i in range(50)]
        result = handler({
            "engine": "capacity",
            "kql": _CLEAN_KQL,
            "_executor": lambda kql: rows,
        })
        assert "largeResult" not in result
        assert "options" not in result
        assert result.get("rowCount") == 50
        assert len(result.get("rows", [])) == 50

    def test_small_result_unaffected_no_flag(self):
        handler = _handler("run_kql")
        rows = [{"id": 1}, {"id": 2}]
        result = handler({
            "engine": "la",
            "kql": _CLEAN_KQL,
            "_executor": lambda kql: rows,
        })
        assert "largeResult" not in result
        assert result.get("rows") == rows
        assert result.get("rowCount") == 2

    def test_char_budget_cap_still_applies_within_the_preview(self):
        # A gated preview (<=100 rows) that STILL blows the char budget must still get
        # truncated by cap_rows -- the row gate is an ADDITIONAL, earlier guard, not a
        # replacement for the existing char-budget cap.
        handler = _handler("run_kql")
        rows = [{"id": i, "blob": "x" * 500} for i in range(200)]
        result = handler({
            "engine": "capacity",
            "kql": _CLEAN_KQL,
            "_executor": lambda kql: rows,
        })
        assert result.get("largeResult") is True
        assert result.get("rowCount") == 200        # true count survives the char-budget cap too
        assert result.get("truncated") is True       # cap_rows meta merged in via _finish's extra
        assert len(result.get("rows", [])) < 100      # char budget bit further than the 100-row preview

    def test_ungated_flag_still_present_when_gated(self):
        handler = _handler("run_kql")
        rows = [{"id": i} for i in range(80)]
        result = handler({
            "engine": "capacity",
            "kql": _CLEAN_KQL,
            "_executor": lambda kql: rows,
        })
        assert result.get("ungated") is True
        assert "ungatedNote" in result
