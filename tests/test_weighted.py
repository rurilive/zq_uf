import importlib
import os
import tempfile
import unittest

from qualityscore import configure, score, trend

module = importlib.import_module("qualityscore.score")


class Base(unittest.TestCase):
    def setUp(self):
        module._history.clear()
        module._config.update(module.DEFAULT_CONFIG)


class WeightedScoreTest(Base):
    def test_weighted(self):
        report = score({"rules": [
            {"name": "not_null", "weight": 3, "failed": True},
            {"name": "unique", "weight": 1, "failed": False},
        ]})
        self.assertEqual(report["score"], 0.25)

    def test_failed_rows_ratio(self):
        report = score({"rules": [
            {"name": "r", "failed_rows": 2, "total_rows": 10},
        ]})
        self.assertEqual(report["score"], 0.8)

    def test_breakdown_carries_weight_and_level(self):
        report = score({"rules": [
            {"name": "r", "weight": 2, "level": "critical", "failed": True},
        ]})
        item = report["breakdown"][0]
        self.assertEqual(item["weight"], 2)
        self.assertEqual(item["level"], "critical")
        self.assertEqual(item["score"], 0.0)


class TrendTest(Base):
    def _score(self, day, fails):
        rules = [{"failed": i < fails} for i in range(4)]
        return score({"dataset": "d", "date": day, "rules": rules})

    def test_detail_has_delta_and_breakdown(self):
        self._score("2026-11-13", 0)
        self._score("2026-11-14", 2)
        detail = trend("d", detail=True)
        self.assertEqual([i["delta"] for i in detail], [0.0, -0.5])
        self.assertEqual(detail[-1]["date"], "2026-11-14")
        self.assertEqual(len(detail[-1]["breakdown"]), 4)

    def test_window_limits_lookback(self):
        for day in range(1, 6):
            self._score("2026-11-%02d" % day, 0)
        self.assertEqual(len(trend("d", window=3)), 3)
        self.assertEqual(len(trend("d")), 5)  # 默认窗口 7 覆盖全部

    def test_same_day_overwrites(self):
        self._score("2026-11-14", 2)
        self._score("2026-11-14", 0)
        self.assertEqual(trend("d"), [1.0])

    def test_trend_summary_in_report(self):
        self._score("2026-11-13", 0)
        report = self._score("2026-11-14", 1)
        self.assertEqual(report["trend"]["delta"], -0.25)
        self.assertEqual(report["trend"]["direction"], "down")
        self.assertEqual(report["trend"]["consecutive_decline"], 1)


class AlertTest(Base):
    def _score(self, day, fails):
        rules = [{"failed": i < fails} for i in range(4)]
        return score({"dataset": "d", "date": day, "rules": rules})

    def test_below_threshold_alert(self):
        configure(threshold=0.9)
        report = self._score("2026-11-14", 2)
        self.assertIn("below_threshold", [a["type"] for a in report["alerts"]])

    def test_no_alert_above_threshold(self):
        configure(threshold=0.4)
        report = self._score("2026-11-14", 2)
        self.assertEqual(report["alerts"], [])

    def test_consecutive_decline_alert(self):
        configure(decline_days=2)
        self._score("2026-11-12", 0)
        first = self._score("2026-11-13", 1)
        self.assertNotIn("consecutive_decline", [a["type"] for a in first["alerts"]])
        second = self._score("2026-11-14", 2)
        self.assertIn("consecutive_decline", [a["type"] for a in second["alerts"]])

    def test_single_day_drop_no_alert(self):
        configure(decline_days=3)
        self._score("2026-11-13", 0)
        report = self._score("2026-11-14", 3)
        self.assertNotIn("consecutive_decline", [a["type"] for a in report["alerts"]])

    def test_decline_drivers_drilldown(self):
        score({"dataset": "d", "date": "2026-11-13", "rules": [
            {"name": "a", "weight": 2, "failed": False},
            {"name": "b", "weight": 1, "failed": False},
        ]})
        report = score({"dataset": "d", "date": "2026-11-14", "rules": [
            {"name": "a", "weight": 2, "failed": True},
            {"name": "b", "weight": 1, "failed": True},
        ]})
        drivers = report["decline_drivers"]
        self.assertEqual(drivers[0]["rule"], "a")  # 权重大排前面
        self.assertEqual(drivers[0]["impact"], 2.0)


class HistoryFileTest(Base):
    def tearDown(self):
        configure(history_file=None)

    def test_persist_and_reload(self):
        path = tempfile.mktemp(suffix=".json")
        try:
            configure(history_file=path)
            score({"dataset": "d", "date": "2026-11-14", "rules": [{"failed": False}]})
            module._history.clear()
            self.assertEqual(trend("d"), [])
            configure(history_file=path)  # 重新加载留档
            self.assertEqual(trend("d"), [1.0])
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
