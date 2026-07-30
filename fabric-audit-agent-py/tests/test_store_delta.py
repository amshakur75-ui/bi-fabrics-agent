"""Tests for store_delta.py — Delta-backed store adapter.

Uses a mock SparkSession since PySpark is not available in the local test environment.
Verifies the store contract (history/append), row conversion, and fallback behavior.
"""
import json
import unittest
from unittest.mock import MagicMock, patch

from fabric_audit_agent.adapters.store_delta import (
    _to_delta_row,
    _from_delta_row,
    create_delta_store,
)


class TestRowConversion(unittest.TestCase):

    def _sample_run(self):
        return {
            "runAt": "2026-07-29T12:00:00Z",
            "tenant": "test-tenant",
            "metrics": {"peakCuPct": 85.3},
            "verdictDecision": "optimize",
            "slaBreachedCount": 2,
            "durationMs": 15420.5,
            "errored": False,
            "tokenUsage": {"input": 500, "output": 200},
            "findings": [
                {"key": "cap.conc", "level": "Warning", "where": "cap-1",
                 "what": "High concentration", "suppressed": False},
            ],
        }

    def test_to_delta_row_maps_camel_to_snake(self):
        row = _to_delta_row(self._sample_run())
        self.assertEqual(row["run_at"], "2026-07-29T12:00:00Z")
        self.assertEqual(row["tenant"], "test-tenant")
        self.assertAlmostEqual(row["peak_cu_pct"], 85.3)
        self.assertEqual(row["verdict_decision"], "optimize")
        self.assertEqual(row["sla_breached_count"], 2)
        self.assertAlmostEqual(row["duration_ms"], 15420.5)
        self.assertFalse(row["errored"])
        self.assertEqual(json.loads(row["token_usage"]), {"input": 500, "output": 200})
        findings = json.loads(row["findings_json"])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["key"], "cap.conc")

    def test_roundtrip_preserves_data(self):
        original = self._sample_run()
        row = _to_delta_row(original)
        restored = _from_delta_row(row)
        self.assertEqual(restored["runAt"], original["runAt"])
        self.assertEqual(restored["tenant"], original["tenant"])
        self.assertEqual(restored["metrics"]["peakCuPct"], original["metrics"]["peakCuPct"])
        self.assertEqual(restored["verdictDecision"], original["verdictDecision"])
        self.assertEqual(restored["slaBreachedCount"], original["slaBreachedCount"])
        self.assertEqual(restored["tokenUsage"], original["tokenUsage"])
        self.assertEqual(len(restored["findings"]), len(original["findings"]))

    def test_null_token_usage_roundtrips(self):
        run = self._sample_run()
        run["tokenUsage"] = None
        row = _to_delta_row(run)
        self.assertIsNone(row["token_usage"])
        restored = _from_delta_row(row)
        self.assertIsNone(restored["tokenUsage"])

    def test_empty_findings_roundtrip(self):
        run = self._sample_run()
        run["findings"] = []
        row = _to_delta_row(run)
        restored = _from_delta_row(row)
        self.assertEqual(restored["findings"], [])

    def test_missing_fields_produce_none(self):
        row = _to_delta_row({})
        self.assertIsNone(row["run_at"])
        self.assertIsNone(row["tenant"])
        self.assertIsNone(row["peak_cu_pct"])


class TestCreateDeltaStore(unittest.TestCase):

    def _mock_spark(self, rows=None):
        spark = MagicMock()
        mock_df = MagicMock()
        if rows is not None:
            mock_rows = []
            for r in rows:
                mock_row = MagicMock()
                mock_row.asDict.return_value = r
                mock_rows.append(mock_row)
            mock_df.orderBy.return_value.limit.return_value.collect.return_value = mock_rows
        else:
            mock_df.orderBy.return_value.limit.return_value.collect.return_value = []
        spark.table.return_value = mock_df
        spark.createDataFrame.return_value = MagicMock()
        return spark

    def test_history_returns_empty_on_no_rows(self):
        spark = self._mock_spark(rows=[])
        store = create_delta_store("main", "bi_fabrics_agent", spark=spark)
        result = store["history"]()
        self.assertEqual(result, [])

    def test_history_returns_converted_rows(self):
        delta_row = {
            "run_at": "2026-07-29T12:00:00Z",
            "tenant": "t1",
            "peak_cu_pct": 90.0,
            "verdict_decision": "healthy",
            "sla_breached_count": 0,
            "duration_ms": 5000.0,
            "errored": False,
            "token_usage": None,
            "findings_json": "[]",
        }
        spark = self._mock_spark(rows=[delta_row])
        store = create_delta_store("main", "bi_fabrics_agent", spark=spark)
        result = store["history"]()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["runAt"], "2026-07-29T12:00:00Z")
        self.assertEqual(result[0]["verdictDecision"], "healthy")

    def test_history_returns_empty_on_exception(self):
        spark = MagicMock()
        spark.table.side_effect = Exception("table not found")
        store = create_delta_store("main", "bi_fabrics_agent", spark=spark)
        result = store["history"]()
        self.assertEqual(result, [])

    def test_append_writes_to_delta(self):
        spark = self._mock_spark()
        mock_count_df = MagicMock()
        mock_count_df.count.return_value = 5
        spark.table.return_value = mock_count_df
        store = create_delta_store("main", "bi_fabrics_agent", spark=spark)
        run = {
            "runAt": "2026-07-29T12:00:00Z",
            "tenant": "t1",
            "metrics": {"peakCuPct": 80.0},
            "verdictDecision": "healthy",
            "slaBreachedCount": 0,
            "durationMs": 3000.0,
            "errored": False,
            "tokenUsage": None,
            "findings": [],
        }
        store["append"](run)
        spark.createDataFrame.assert_called_once()
        created_df = spark.createDataFrame.return_value
        created_df.write.mode.assert_called_with("append")

    def test_store_has_correct_interface(self):
        spark = self._mock_spark()
        store = create_delta_store("main", "bi_fabrics_agent", spark=spark)
        self.assertIn("history", store)
        self.assertIn("append", store)
        self.assertTrue(callable(store["history"]))
        self.assertTrue(callable(store["append"]))


class TestJobFallback(unittest.TestCase):

    def test_default_store_falls_back_to_local_without_delta_config(self):
        from fabric_audit_agent.job import _default_store
        store = _default_store({})
        self.assertIn("history", store)
        self.assertIn("append", store)

    def test_default_store_falls_back_on_import_error(self):
        from fabric_audit_agent.job import _default_store
        env = {"FABRIC_DELTA_CATALOG": "main", "FABRIC_DELTA_SCHEMA": "test"}
        store = _default_store(env)
        self.assertIn("history", store)
        self.assertIn("append", store)
