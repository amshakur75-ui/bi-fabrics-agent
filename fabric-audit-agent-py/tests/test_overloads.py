"""overload_windows — capacity-level over-threshold windows with interactive/background split
and contributors. Pure math on epoch seconds."""
import unittest

from fabric_audit_agent.investigation.overloads import (
    window_start, _op_window_contributions, overload_windows,
)

W = 30


class TestWindowing(unittest.TestCase):
    def test_window_start_floors_to_30s(self):
        self.assertEqual(window_start(0), 0)
        self.assertEqual(window_start(29), 0)
        self.assertEqual(window_start(30), 30)
        self.assertEqual(window_start(61), 60)

    def test_op_within_one_window_gets_all_cu(self):
        op = {"startEpoch": 5, "endEpoch": 20, "cuSeconds": 100.0}
        self.assertEqual(list(_op_window_contributions(op)), [(0, 100.0)])

    def test_op_spanning_two_windows_splits_linearly(self):
        # 20s in [0,30) and 20s in [30,60): 40s total, 100 CU -> 50/50
        op = {"startEpoch": 10, "endEpoch": 50, "cuSeconds": 100.0}
        contribs = dict(_op_window_contributions(op))
        self.assertAlmostEqual(contribs[0], 50.0, places=6)
        self.assertAlmostEqual(contribs[30], 50.0, places=6)

    def test_zero_duration_lands_in_start_window(self):
        op = {"startEpoch": 45, "endEpoch": 45, "cuSeconds": 12.0}
        self.assertEqual(list(_op_window_contributions(op)), [(30, 12.0)])


class TestOverloadWindows(unittest.TestCase):
    def test_only_windows_over_threshold_returned_ranked(self):
        series = [{"epoch": 0, "cuPct": 80.0}, {"epoch": 30, "cuPct": 140.0},
                  {"epoch": 60, "cuPct": 105.0}]
        out = overload_windows(series, [], base_cu=1024, min_cu_pct=100)
        self.assertEqual([w["windowEpoch"] for w in out], [30, 60])   # 80% dropped, sorted desc

    def test_interactive_and_background_split_from_ops(self):
        # F1024: window budget = 1024*30 = 30,720 CU-sec. One op contributes 15,360 CU-sec to the
        # window -> interactive 50%. Total 140% -> background 90%.
        series = [{"epoch": 30, "cuPct": 140.0}]
        ops = [{"startEpoch": 30, "endEpoch": 60, "cuSeconds": 15360.0,
                "user": "paul", "item": "Sales", "operation": "MdxQuery"}]
        out = overload_windows(series, ops, base_cu=1024, min_cu_pct=100)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["interactiveCuPct"], 50.0, places=1)
        self.assertAlmostEqual(out[0]["backgroundCuPct"], 90.0, places=1)
        self.assertEqual(out[0]["contributors"][0]["user"], "paul")

    def test_background_dominated_window_has_low_interactive(self):
        # 8,964% total, tiny user load -> background ~= total (the real 8,964% spike shape)
        series = [{"epoch": 0, "cuPct": 8964.4}]
        ops = [{"startEpoch": 0, "endEpoch": 30, "cuSeconds": 519.0,
                "user": "jake", "item": "Sales", "operation": "MdxQuery"}]
        out = overload_windows(series, ops, base_cu=1024, min_cu_pct=1000)
        self.assertEqual(len(out), 1)
        self.assertLess(out[0]["interactiveCuPct"], 5.0)          # user load explains almost nothing
        self.assertGreater(out[0]["backgroundCuPct"], 8900.0)     # the story is background

    def test_contributors_ranked_and_capped(self):
        series = [{"epoch": 0, "cuPct": 120.0}]
        ops = [{"startEpoch": 0, "endEpoch": 30, "cuSeconds": c, "user": f"u{c}",
                "item": "Sales", "operation": "MdxQuery"} for c in (100.0, 900.0, 500.0)]
        out = overload_windows(series, ops, base_cu=1024, min_cu_pct=100, top_contributors=2)
        users = [c["user"] for c in out[0]["contributors"]]
        self.assertEqual(users, ["u900.0", "u500.0"])   # ranked by CU in window, capped at 2

    def test_unknown_base_gives_total_only(self):
        series = [{"epoch": 0, "cuPct": 150.0}]
        ops = [{"startEpoch": 0, "endEpoch": 30, "cuSeconds": 5000.0, "user": "x"}]
        out = overload_windows(series, ops, base_cu=None, min_cu_pct=100)
        self.assertEqual(out[0]["totalCuPct"], 150.0)
        self.assertIsNone(out[0]["interactiveCuPct"])
        self.assertIsNone(out[0]["backgroundCuPct"])


if __name__ == "__main__":
    unittest.main()
