import importlib
import unittest

from qualityscore import configure, report, reset_config, score, trend

module = importlib.import_module("qualityscore.score")


class ScoreTest(unittest.TestCase):
    def setUp(self):
        module._history.clear()

    def test_all_pass(self):
        self.assertEqual(score({"rules": [{"failed": False}]})["score"], 1.0)

    def test_half_fail(self):
        self.assertEqual(score({"rules": [{"failed": True}, {"failed": False}]})["score"], 0.5)

    def test_trend_appends(self):
        score({"dataset": "d", "rules": [{"failed": False}]})
        self.assertEqual(trend("d"), [1.0])


class WeightedScoreTest(unittest.TestCase):
    def setUp(self):
        module._history.clear()
        reset_config()

    def tearDown(self):
        reset_config()

    def test_weighted_score(self):
        result = score({"rules": [
            {"name": "pk", "failed": True, "weight": 3.0},
            {"name": "fmt", "failed": False, "weight": 1.0},
        ]})
        self.assertEqual(result["score"], 0.25)

    def test_failed_rows_ratio(self):
        result = score({"rows": 100, "rules": [
            {"name": "not_null", "failed": 25, "weight": 1.0},
        ]})
        self.assertEqual(result["score"], 0.75)
        self.assertEqual(result["breakdown"][0]["failed"], 25)

    def test_breakdown_has_weight_and_contribution(self):
        result = score({"rules": [
            {"name": "pk", "failed": False, "weight": 2.0, "level": "critical"},
            {"name": "fmt", "failed": True, "weight": 1.0},
        ]})
        pk, fmt = result["breakdown"]
        self.assertEqual(pk["weight"], 2.0)
        self.assertEqual(pk["level"], "critical")
        self.assertAlmostEqual(pk["contribution"] + fmt["contribution"], result["score"], places=2)


class TrendTest(unittest.TestCase):
    def setUp(self):
        module._history.clear()
        reset_config()

    def tearDown(self):
        reset_config()

    def _score_on(self, dataset, day, failed_count):
        return score({"dataset": dataset, "date": day, "rules": [
            {"name": "r%d" % i, "failed": i < failed_count}
            for i in range(4)
        ]})

    def test_trend_delta_and_direction(self):
        self._score_on("d", "2026-11-13", 0)
        result = self._score_on("d", "2026-11-14", 2)
        self.assertEqual(result["trend"]["delta"], -0.5)
        self.assertEqual(result["trend"]["direction"], "down")

    def test_trend_lookback_window(self):
        for i, day in enumerate(["2026-11-11", "2026-11-12", "2026-11-13"]):
            self._score_on("d", day, i)
        self.assertEqual(trend("d"), [1.0, 0.75, 0.5])
        self.assertEqual(trend("d", days=2), [0.75, 0.5])

    def test_same_day_overwrites(self):
        self._score_on("d", "2026-11-13", 0)
        self._score_on("d", "2026-11-13", 4)
        self.assertEqual(trend("d"), [0.0])

    def test_consecutive_decline_alert(self):
        configure(decline_days=2)
        self._score_on("d", "2026-11-11", 0)
        r2 = self._score_on("d", "2026-11-12", 1)
        self.assertNotIn("consecutive_decline", [a["type"] for a in r2["alerts"]])
        r3 = self._score_on("d", "2026-11-13", 2)
        types = [a["type"] for a in r3["alerts"]]
        self.assertIn("consecutive_decline", types)
        self.assertEqual(r3["trend"]["consecutive_declines"], 2)

    def test_no_alert_on_single_drop(self):
        configure(decline_days=3)
        self._score_on("d", "2026-11-11", 0)
        self._score_on("d", "2026-11-12", 2)
        result = self._score_on("d", "2026-11-13", 1)
        self.assertNotIn("consecutive_decline", [a["type"] for a in result["alerts"]])

    def test_threshold_alert_configurable(self):
        configure(threshold=0.9)
        result = self._score_on("d", "2026-11-13", 1)
        self.assertIn("below_threshold", [a["type"] for a in result["alerts"]])
        configure(threshold=0.5)
        module._history.clear()
        result = self._score_on("d", "2026-11-13", 1)
        self.assertEqual(result["alerts"], [])

    def test_decline_drilldown(self):
        score({"dataset": "d", "date": "2026-11-11", "rows": 100, "rules": [
            {"name": "pk", "failed": 0, "weight": 1.0},
            {"name": "fmt", "failed": 0, "weight": 1.0},
        ]})
        result = score({"dataset": "d", "date": "2026-11-12", "rows": 100, "rules": [
            {"name": "pk", "failed": 50, "weight": 1.0},
            {"name": "fmt", "failed": 0, "weight": 1.0},
        ]})
        drivers = result["trend"]["drivers"]
        self.assertEqual(drivers[0]["name"], "pk")
        self.assertEqual(drivers[0]["delta"], -0.5)

    def test_report_keeps_daily_breakdown(self):
        self._score_on("d", "2026-11-12", 0)
        self._score_on("d", "2026-11-13", 2)
        rep = report("d")
        self.assertEqual([e["date"] for e in rep["days"]], ["2026-11-12", "2026-11-13"])
        self.assertEqual(len(rep["days"][0]["breakdown"]), 4)
        self.assertIn("window", rep["trend"])


if __name__ == "__main__":
    unittest.main()
