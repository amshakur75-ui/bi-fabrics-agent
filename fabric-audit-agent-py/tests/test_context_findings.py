"""Tests for context_findings.py (Phase 6) — recent audit_findings context injection."""
import unittest
from unittest.mock import MagicMock

from fabric_audit_agent.context_findings import (
    query_recent_findings,
    format_context,
    create_findings_store_delta,
)


class TestQueryRecentFindings(unittest.TestCase):

    def test_returns_empty_when_store_is_none(self):
        self.assertEqual(query_recent_findings(None), [])

    def test_returns_empty_when_store_has_no_query(self):
        self.assertEqual(query_recent_findings({}), [])

    def test_returns_findings_from_store(self):
        findings = [{"findingKey": "k1", "level": "Warning", "whatText": "high CU"}]
        store = {"query": lambda **kw: findings}
        self.assertEqual(query_recent_findings(store), findings)

    def test_passes_scope_and_tenant(self):
        captured = {}
        def mock_query(**kw):
            captured.update(kw)
            return []
        store = {"query": mock_query}
        query_recent_findings(store, scope="cap-1", tenant="t1", limit=3)
        self.assertEqual(captured["scope"], "cap-1")
        self.assertEqual(captured["tenant"], "t1")
        self.assertEqual(captured["limit"], 3)

    def test_returns_empty_on_exception(self):
        store = {"query": lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))}
        def bad_query(**kw):
            raise RuntimeError("boom")
        store = {"query": bad_query}
        self.assertEqual(query_recent_findings(store), [])

    def test_never_blocks_investigation(self):
        store = {"query": lambda **kw: None}
        result = query_recent_findings(store)
        self.assertEqual(result, [])


class TestFormatContext(unittest.TestCase):

    def test_empty_findings_returns_empty_string(self):
        self.assertEqual(format_context([]), "")
        self.assertEqual(format_context(None), "")

    def test_single_finding_formatted(self):
        findings = [{"level": "Warning", "whatText": "High CPU", "runAt": "2026-07-29T12:00:00Z"}]
        text = format_context(findings)
        self.assertIn("1 prior finding", text)
        self.assertIn("[Warning] High CPU", text)
        self.assertIn("seen 2026-07-29T12:00:00Z", text)
        self.assertIn("context only", text)

    def test_multiple_findings_formatted(self):
        findings = [
            {"level": "Warning", "whatText": "High CPU", "runAt": "2026-07-29"},
            {"level": "Critical", "whatText": "Throttling", "runAt": "2026-07-28"},
        ]
        text = format_context(findings)
        self.assertIn("2 prior findings", text)
        self.assertIn("[Warning]", text)
        self.assertIn("[Critical]", text)

    def test_scope_label_included(self):
        findings = [{"level": "Info", "whatText": "test", "runAt": "2026-07-29"}]
        text = format_context(findings, scope="cap-1")
        self.assertIn("for cap-1", text)

    def test_missing_fields_handled(self):
        findings = [{}]
        text = format_context(findings)
        self.assertIn("[Info]", text)
        self.assertIn("(no description)", text)

    def test_context_disclaimer_always_present(self):
        findings = [{"level": "Warning", "whatText": "test", "runAt": "2026-07-29"}]
        text = format_context(findings)
        self.assertIn("context only", text.lower())


class TestFindingsStoreDelta(unittest.TestCase):

    def test_store_has_query_and_write(self):
        spark = MagicMock()
        store = create_findings_store_delta("main", "bi_fabrics_agent", spark=spark)
        self.assertIn("query", store)
        self.assertIn("write", store)
        self.assertTrue(callable(store["query"]))
        self.assertTrue(callable(store["write"]))

    def test_query_returns_formatted_results(self):
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, key: {
            "finding_key": "cap.conc",
            "level": "Warning",
            "what_text": "High concentration",
            "run_at": "2026-07-29T12:00:00Z",
            "resource": "cap-1",
            "confidence": "high",
        }[key]
        spark = MagicMock()
        spark.sql.return_value.collect.return_value = [mock_row]
        store = create_findings_store_delta("main", "bi_fabrics_agent", spark=spark)
        results = store["query"](scope="cap-1", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["findingKey"], "cap.conc")
        self.assertEqual(results[0]["level"], "Warning")

    def test_write_appends_findings(self):
        spark = MagicMock()
        store = create_findings_store_delta("main", "bi_fabrics_agent", spark=spark)
        findings = [
            {"key": "cap.conc", "score": {"level": "Warning"}, "type": "capacity.concentration",
             "resource": "cap-1", "what": "High concentration", "confidence": "high"},
        ]
        store["write"]("2026-07-29T12:00:00Z", "t1", findings)
        spark.createDataFrame.assert_called_once()

    def test_write_skips_empty_findings(self):
        spark = MagicMock()
        store = create_findings_store_delta("main", "bi_fabrics_agent", spark=spark)
        store["write"]("2026-07-29T12:00:00Z", "t1", [])
        spark.createDataFrame.assert_not_called()
