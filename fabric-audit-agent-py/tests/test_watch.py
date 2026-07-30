"""Autonomous watcher — trigger evaluation, harmless/real classification, dedup. Pure/offline."""
import unittest

from fabric_audit_agent.investigation.watch import evaluate_incidents, new_incidents
from fabric_audit_agent.watch_run import plan_watch


def _win(epoch, total, interactive=None, background=None, contributors=None):
    return {"windowEpoch": epoch, "totalCuPct": total, "interactiveCuPct": interactive,
            "backgroundCuPct": background, "contributors": contributors or []}


def _peak(conv, life, user="analyst", item="Ent-Reporting-Sales", op="QueryEnd", detail="MdxQuery"):
    return {"pctBaseConverted": conv, "pctBaseLifetime": life, "user": user, "item": item,
            "operation": op, "operationDetail": detail, "durationMs": 300000, "cuSeconds": 5000,
            "ts": "2026-07-17T14:03:00Z"}


class TestCapacityTrigger(unittest.TestCase):
    def test_sustained_overage_is_a_real_warn(self):
        wins = [_win(0, 130, interactive=125, background=5),
                _win(30, 135, interactive=130, background=5),
                _win(60, 140, interactive=132, background=8,
                     contributors=[{"user": "analyst", "item": "Sales", "cuInWindow": 900}])]
        inc = evaluate_incidents(wins, [], base_cu=1024)
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0]["severity"], "warn")
        self.assertEqual(inc[0]["emoji"], "⚠️")
        self.assertIn("worth attention", inc[0]["why"].lower())

    def test_brief_background_blip_is_harmless_info(self):
        wins = [_win(0, 108, interactive=4, background=104)]
        inc = evaluate_incidents(wins, [], base_cu=1024)
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0]["severity"], "info")
        self.assertEqual(inc[0]["emoji"], "✅")
        self.assertIn("no", inc[0]["why"].lower())

    def test_no_overage_no_incident(self):
        wins = [_win(0, 80, interactive=40, background=40), _win(30, 95, interactive=50, background=45)]
        self.assertEqual(evaluate_incidents(wins, [], base_cu=1024), [])


class TestOperationTrigger(unittest.TestCase):
    def test_op_over_30_converted_fires(self):
        inc = evaluate_incidents([], [_peak(47.1, 471.2)], base_cu=1024)
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0]["kind"], "operation")
        self.assertEqual(inc[0]["severity"], "warn")
        self.assertIn("47.1% (471.2%)", inc[0]["summary"])

    def test_op_under_30_converted_silent(self):
        self.assertEqual(evaluate_incidents([], [_peak(12.0, 120.0)], base_cu=1024), [])


class TestDedup(unittest.TestCase):
    def test_new_incidents_filters_seen(self):
        inc = evaluate_incidents([], [_peak(47.1, 471.2)], base_cu=1024)
        seen = {inc[0]["id"]}
        self.assertEqual(new_incidents(inc, seen), [])
        self.assertEqual(len(new_incidents(inc, set())), 1)


class TestPlanWatch(unittest.TestCase):
    def test_end_to_end_pure(self):
        events = [{"ts": "2026-07-17T14:03:00Z", "user": "analyst@example.com", "item": "Sales",
                   "operation": "QueryEnd", "operationDetail": "MdxQuery", "kind": "interactive",
                   "cuSeconds": 5000, "durationMs": 300000}]
        fresh = plan_watch([], events, base_cu=1024, seen_ids=set())
        self.assertTrue(any(i["kind"] == "operation" for i in fresh))


if __name__ == "__main__":
    unittest.main()
