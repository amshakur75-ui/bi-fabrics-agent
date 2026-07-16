"""timepoint_peaks — the Capacity Metrics app "% of base capacity" lens.

Ground-truth numbers are taken from a real F1024 Timepoint Detail screenshot, so these tests
pin the formula to what the app actually shows (not the totalCu/base miscalculation that produced
impossible 471% figures in the field).
"""
import unittest

from fabric_audit_agent.investigation.timepoint_peaks import (
    base_cu_from_sku, timepoint_pct_base, timepoint_peaks,
)


class TestBaseCuFromSku(unittest.TestCase):
    def test_f_sku_parses_integer(self):
        self.assertEqual(base_cu_from_sku("F1024"), 1024)
        self.assertEqual(base_cu_from_sku("F64"), 64)
        self.assertEqual(base_cu_from_sku("f2"), 2)  # case-insensitive

    def test_p_sku_maps(self):
        self.assertEqual(base_cu_from_sku("P1"), 64)
        self.assertEqual(base_cu_from_sku("P5"), 1024)

    def test_trial_or_unknown_is_none(self):
        self.assertIsNone(base_cu_from_sku("FTL64"))   # trial capacity
        self.assertIsNone(base_cu_from_sku(""))
        self.assertIsNone(base_cu_from_sku(None))


class TestTimepointPctBase(unittest.TestCase):
    def test_matches_metrics_app_screenshot_rows_F1024(self):
        # Ground truth from the app's Timepoint Detail (F1024, base 1024):
        #   total 54,302.75 -> timepoint 5,430.2752 -> 17.68% of base
        self.assertAlmostEqual(timepoint_pct_base(54302.75, 1024), 17.68, places=2)
        #   total 17,400.00 -> 5.66%
        self.assertAlmostEqual(timepoint_pct_base(17400.00, 1024), 5.66, places=2)
        #   total 13,616.75 -> 4.43%
        self.assertAlmostEqual(timepoint_pct_base(13616.75, 1024), 4.43, places=2)

    def test_none_when_base_unknown(self):
        self.assertIsNone(timepoint_pct_base(50000, None))
        self.assertIsNone(timepoint_pct_base(50000, 0))


class TestTimepointPeaks(unittest.TestCase):
    def _ev(self, cu, user="u@co", kind="interactive", op="QueryEnd", item="Sales"):
        return {"ts": "2026-07-16T14:09:30Z", "user": user, "item": item, "operation": op,
                "kind": kind, "cuSeconds": cu, "durationMs": 368000}

    def test_ranks_by_pct_base_and_reports_both_columns(self):
        events = [self._ev(4825.28, user="paul"), self._ev(4606.81, user="damian"),
                  self._ev(4063.75, user="marc")]
        peaks = timepoint_peaks(events, base_cu=1024, top_n=10)
        self.assertEqual([p["user"] for p in peaks], ["paul", "damian", "marc"])
        top = peaks[0]
        # both the "size" (cuSeconds) and the "intensity" (pctBase) columns are present
        self.assertEqual(top["cuSeconds"], 4825.28)
        # timepoint lens: total/10 smoothing, over the 30-sec base budget
        self.assertAlmostEqual(top["pctBase"], 4825.28 / 10 / (1024 * 30) * 100, places=2)
        self.assertAlmostEqual(top["pctBase"], 1.57, places=2)   # ~1.57%, NOT the 471% the agent reported
        self.assertEqual(top["timepointCuSeconds"], round(4825.28 / 10, 4))

    def test_min_pct_base_filter(self):
        events = [self._ev(60000), self._ev(1000)]   # ~1.95% and ~0.03%
        peaks = timepoint_peaks(events, base_cu=1024, min_pct_base=1.0)
        self.assertEqual(len(peaks), 1)
        self.assertEqual(peaks[0]["cuSeconds"], 60000)

    def test_refresh_excluded_by_default(self):
        events = [self._ev(9999, kind="refresh"), self._ev(100, kind="interactive")]
        peaks = timepoint_peaks(events, base_cu=1024)
        self.assertEqual([p["kind"] for p in peaks], ["interactive"])
        peaks_incl = timepoint_peaks(events, base_cu=1024, include_refresh=True)
        self.assertEqual(len(peaks_incl), 2)

    def test_top_n_caps_rows(self):
        events = [self._ev(1000 + i, user=f"u{i}") for i in range(30)]
        self.assertEqual(len(timepoint_peaks(events, base_cu=1024, top_n=5)), 5)

    def test_unknown_base_still_ranks_by_cu_with_null_pct(self):
        events = [self._ev(4000), self._ev(9000)]
        peaks = timepoint_peaks(events, base_cu=None)
        self.assertEqual([p["cuSeconds"] for p in peaks], [9000, 4000])
        self.assertIsNone(peaks[0]["pctBase"])


if __name__ == "__main__":
    unittest.main()
