"""timepoint_peaks — the two capacity-peak lenses.

LIFETIME lens (cuSeconds/base*100) is the "471% / above 300%" operation-cost view the field
relies on for thresholding. TIMEPOINT lens (cuSeconds/10/(base*30)*100) matches the Capacity
Metrics app's Timepoint Detail column; its numbers are pinned to a real F1024 screenshot.
"""
import unittest

from fabric_audit_agent.investigation.timepoint_peaks import (
    base_cu_from_sku, lifetime_pct_base, timepoint_pct_base, timepoint_peaks,
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


class TestLenses(unittest.TestCase):
    def test_lifetime_lens_matches_the_471_style_figures(self):
        # F1024: 4,825.28 CU-sec -> 471.2% (the field's beloved "above 300%" table)
        self.assertAlmostEqual(lifetime_pct_base(4825.28, 1024), 471.2, places=1)
        self.assertAlmostEqual(lifetime_pct_base(1048.29, 1024), 102.4, places=1)

    def test_timepoint_lens_matches_metrics_app_screenshot_F1024(self):
        # Ground truth from the app's Timepoint Detail (F1024, base 1024):
        self.assertAlmostEqual(timepoint_pct_base(54302.75, 1024), 17.68, places=2)
        self.assertAlmostEqual(timepoint_pct_base(17400.00, 1024), 5.66, places=2)
        self.assertAlmostEqual(timepoint_pct_base(13616.75, 1024), 4.43, places=2)

    def test_none_when_base_unknown(self):
        self.assertIsNone(lifetime_pct_base(50000, None))
        self.assertIsNone(timepoint_pct_base(50000, 0))


class TestTimepointPeaks(unittest.TestCase):
    def _ev(self, cu, user="u@co", kind="interactive", op="QueryEnd", item="Sales"):
        return {"ts": "2026-07-16T14:09:30Z", "user": user, "item": item, "operation": op,
                "kind": kind, "cuSeconds": cu, "durationMs": 368000}

    def test_row_carries_both_lenses(self):
        peaks = timepoint_peaks([self._ev(4825.28, user="paul")], base_cu=1024)
        top = peaks[0]
        self.assertEqual(top["cuSeconds"], 4825.28)
        self.assertAlmostEqual(top["pctBaseLifetime"], 471.2, places=1)     # the 471% view
        self.assertAlmostEqual(top["pctBaseTimepoint"], 1.57, places=2)     # the timepoint view
        self.assertEqual(top["timepointCuSeconds"], round(4825.28 / 10, 4))

    def test_lifetime_threshold_reproduces_above_300_table(self):
        events = [self._ev(4825.28, user="paul"), self._ev(2000.0, user="small")]
        # >300% lifetime keeps only the 471% op, drops the 195% one
        peaks = timepoint_peaks(events, base_cu=1024, min_pct=300, lens="lifetime")
        self.assertEqual([p["user"] for p in peaks], ["paul"])

    def test_timepoint_threshold_uses_timepoint_pct(self):
        # 60,000 CU-sec -> ~19.5% timepoint; 1,000 -> ~0.3%. >=10% timepoint keeps only the first.
        events = [self._ev(60000), self._ev(1000)]
        peaks = timepoint_peaks(events, base_cu=1024, min_pct=10, lens="timepoint")
        self.assertEqual(len(peaks), 1)
        self.assertEqual(peaks[0]["cuSeconds"], 60000)

    def test_ranks_by_cu_regardless_of_lens(self):
        events = [self._ev(4063.75, user="marc"), self._ev(4825.28, user="paul"),
                  self._ev(4606.81, user="damian")]
        for lens in ("lifetime", "timepoint"):
            peaks = timepoint_peaks(events, base_cu=1024, lens=lens)
            self.assertEqual([p["user"] for p in peaks], ["paul", "damian", "marc"])

    def test_refresh_excluded_by_default(self):
        events = [self._ev(9999, kind="refresh"), self._ev(100, kind="interactive")]
        self.assertEqual([p["kind"] for p in timepoint_peaks(events, base_cu=1024)], ["interactive"])
        self.assertEqual(len(timepoint_peaks(events, base_cu=1024, include_refresh=True)), 2)

    def test_top_n_caps_rows(self):
        events = [self._ev(1000 + i, user=f"u{i}") for i in range(30)]
        self.assertEqual(len(timepoint_peaks(events, base_cu=1024, top_n=5)), 5)

    def test_unknown_base_ranks_by_cu_with_null_pcts(self):
        peaks = timepoint_peaks([self._ev(4000), self._ev(9000)], base_cu=None)
        self.assertEqual([p["cuSeconds"] for p in peaks], [9000, 4000])
        self.assertIsNone(peaks[0]["pctBaseLifetime"])
        self.assertIsNone(peaks[0]["pctBaseTimepoint"])

    def test_bad_lens_raises(self):
        with self.assertRaises(ValueError):
            timepoint_peaks([self._ev(100)], base_cu=1024, lens="bogus")


if __name__ == "__main__":
    unittest.main()
