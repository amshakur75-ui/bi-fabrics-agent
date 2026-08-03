"""Test coverage for the NL-to-query target classifier (KQL vs SQL vs DAX)."""

import pytest

from fabric_audit_agent.query.target_classifier import classify


class TestClassifyKql:
    """Questions that should route to KQL."""

    def test_explicit_kql_mention(self):
        result = classify("Run a KQL query to find high-CPU events")
        assert result["target"] == "kql"
        assert result["confidence"] > 0.5

    def test_eventhouse_mention(self):
        result = classify("Query the Eventhouse for capacity data")
        assert result["target"] == "kql"

    def test_log_analytics_mention(self):
        result = classify("Search Log Analytics for failed operations")
        assert result["target"] == "kql"

    def test_kusto_mention(self):
        result = classify("Use Kusto to find events in the last hour")
        assert result["target"] == "kql"

    def test_timegenerated_column(self):
        result = classify("Show me all rows where TimeGenerated is after yesterday")
        assert result["target"] == "kql"

    def test_cputimems_column(self):
        result = classify("What operations had the highest CpuTimeMs?")
        assert result["target"] == "kql"

    def test_capacity_events(self):
        result = classify("Look at capacity events for the last 24 hours")
        assert result["target"] == "kql"

    def test_workspace_monitoring(self):
        result = classify("Check workspace monitoring data for anomalies")
        assert result["target"] == "kql"

    def test_adx_mention(self):
        result = classify("Query ADX for the latest telemetry")
        assert result["target"] == "kql"


class TestClassifySql:
    """Questions that should route to SQL."""

    def test_explicit_sql_mention(self):
        result = classify("Write a SQL query against the lakehouse")
        assert result["target"] == "sql"
        assert result["confidence"] > 0.5

    def test_lakehouse_mention(self):
        result = classify("Query the lakehouse for customer data")
        assert result["target"] == "sql"

    def test_warehouse_mention(self):
        result = classify("What's in the data warehouse sales table?")
        assert result["target"] == "sql"

    def test_sql_endpoint_mention(self):
        result = classify("Connect to the SQL endpoint to read inventory")
        assert result["target"] == "sql"

    def test_delta_table_mention(self):
        result = classify("Read from the delta table containing orders")
        assert result["target"] == "sql"

    def test_onelake_mention(self):
        result = classify("Query data stored in OneLake")
        assert result["target"] == "sql"

    def test_tsql_mention(self):
        result = classify("Write T-SQL to find missing records")
        assert result["target"] == "sql"

    def test_fact_table_mention(self):
        result = classify("Show me all records from the fact table for sales")
        assert result["target"] == "sql"


class TestClassifyDax:
    """Questions that should route to DAX."""

    def test_explicit_dax_mention(self):
        result = classify("Write a DAX query to calculate total revenue")
        assert result["target"] == "dax"
        assert result["confidence"] > 0.5

    def test_measure_mention(self):
        result = classify("What measures are defined in the Sales model?")
        assert result["target"] == "dax"

    def test_semantic_model_mention(self):
        result = classify("Query the semantic model for product categories")
        assert result["target"] == "dax"

    def test_power_bi_report_mention(self):
        result = classify("Get data from the Power BI report's underlying model using DAX")
        assert result["target"] == "dax"

    def test_evaluate_mention(self):
        result = classify("Run an EVALUATE against the Sales dataset")
        assert result["target"] == "dax"

    def test_xmla_mention(self):
        result = classify("Query via XMLA for the revenue breakdown")
        assert result["target"] == "dax"

    def test_calculatetable_mention(self):
        result = classify("Use CALCULATETABLE to filter by region")
        assert result["target"] == "dax"

    def test_filter_context_mention(self):
        result = classify("How does the filter context affect this calculation?")
        assert result["target"] == "dax"

    def test_topn_mention(self):
        result = classify("Use TOPN to find the top 5 products")
        assert result["target"] == "dax"

    def test_summarizecolumns_mention(self):
        result = classify("SUMMARIZECOLUMNS by date and category")
        assert result["target"] == "dax"


class TestClassifyDefault:
    """Questions with no clear indicators should default to KQL."""

    def test_no_indicators(self):
        result = classify("How much data do we have?")
        assert result["target"] == "kql"
        assert result["confidence"] < 0.3

    def test_generic_question(self):
        result = classify("Show me the numbers")
        assert result["target"] == "kql"
        assert result["confidence"] < 0.3


class TestClassifyReturnShape:
    """Verify the return dict always has the required keys."""

    def test_has_target(self):
        result = classify("anything")
        assert "target" in result
        assert result["target"] in ("kql", "sql", "dax")

    def test_has_confidence(self):
        result = classify("anything")
        assert "confidence" in result
        assert 0 <= result["confidence"] <= 1.0

    def test_has_reason(self):
        result = classify("anything")
        assert "reason" in result
        assert isinstance(result["reason"], str)


class TestClassifyEdgeCases:
    """Edge cases and ambiguous queries."""

    def test_empty_string(self):
        result = classify("")
        assert result["target"] == "kql"
        assert result["confidence"] < 0.3

    def test_mixed_kql_and_sql(self):
        """When both KQL and SQL indicators are present, the stronger signal wins."""
        # "Kusto" is a strong KQL indicator, "table" is a weak SQL one
        result = classify("Query the Kusto table for recent events")
        assert result["target"] == "kql"

    def test_mixed_sql_and_dax(self):
        """Strong DAX indicators should win over weak SQL ones."""
        result = classify("Use SUMMARIZECOLUMNS to query the table in the semantic model")
        assert result["target"] == "dax"

    def test_confidence_higher_for_explicit_mention(self):
        """Explicitly mentioning the query language should give higher confidence than
        a generic question with no domain indicators at all."""
        kql_result = classify("Run a KQL query")
        generic_result = classify("How much data is there?")
        assert kql_result["confidence"] > generic_result["confidence"]

    def test_case_insensitive(self):
        """Indicators should match regardless of case."""
        lower = classify("query the lakehouse")
        upper = classify("Query the LAKEHOUSE")
        assert lower["target"] == upper["target"] == "sql"
