import json
import os
import tempfile
import unittest

from qualitytrend import backtest, check, export, feedback, reset, trend


class TrendTest(unittest.TestCase):
    def test_fixed_threshold_alerts(self):
        self.assertTrue(check("d1", {"value": 0.5})["alert"])

    def test_low_value_quiet(self):
        self.assertFalse(check("d2", {"value": 0.01})["alert"])

    def test_trend_has_no_direction(self):
        check("d3", {"value": 0.02})
        self.assertIsNone(trend("d3")["direction"])


class BaselineTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_stable_baseline_no_alert(self):
        for v in [0.010, 0.012] * 5:
            check("base", {"value": v})
        result = check("base", {"value": 0.011})
        self.assertFalse(result["alert"])
        self.assertAlmostEqual(result["baseline"], 0.011, places=2)
        self.assertGreaterEqual(result["samples"], 3)

    def test_spike_alerts_with_context(self):
        for v in [0.010, 0.012] * 5:
            check("spike", {"value": v})
        result = check("spike", {"value": 0.05})
        self.assertTrue(result["alert"])
        self.assertEqual(result["reason"], "deviation")
        self.assertGreater(result["deviation"], 3.0)
        self.assertIsNotNone(result["baseline"])
        self.assertGreater(result["samples"], 0)
        self.assertEqual(result["history"][-1], 0.05)

    def test_baseline_window_configurable(self):
        for v in [0.010, 0.012] * 5:
            check("cfg", {"value": v})
        result = check("cfg", {"value": 0.011}, config={"window": 4})
        self.assertEqual(result["samples"], 4)
        with self.assertRaises(ValueError):
            check("cfg", {"value": 0.011}, config={"nonsense": 1})


class SeasonalTest(unittest.TestCase):
    def setUp(self):
        reset()

    def _feed_weeks(self, weeks):
        for _ in range(weeks):
            for weekday in range(7):
                value = 0.05 if weekday == 0 else 0.01
                check("season", {"value": value, "day": weekday})

    def test_same_weekday_baseline(self):
        self._feed_weeks(4)
        monday = check("season", {"value": 0.05, "day": 0})
        self.assertFalse(monday["alert"])  # 周一冲高是周期常态
        wednesday = check("season", {"value": 0.05, "day": 2})
        self.assertTrue(wednesday["alert"])
        self.assertEqual(wednesday["reason"], "deviation")


class DriftTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_slow_drift_confirmed_by_consecutive_windows(self):
        for v in [0.010, 0.012] * 5:
            check("drift", {"value": v})
        results = [check("drift", {"value": v}) for v in (0.0135, 0.0140, 0.0145)]
        self.assertFalse(results[0]["alert"])
        self.assertFalse(results[1]["alert"])
        self.assertTrue(results[2]["alert"])
        self.assertEqual(results[2]["reason"], "drift")


class DedupTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_same_reason_alerts_deduped(self):
        for v in [0.010, 0.012] * 5:
            check("dup", {"value": v})
        first = check("dup", {"value": 0.05})
        second = check("dup", {"value": 0.06})
        self.assertTrue(first["alert"])
        self.assertFalse(second["alert"])
        self.assertTrue(second["deduped"])
        check("dup", {"value": 0.011})  # 平静一次后解除去重
        third = check("dup", {"value": 0.09})
        self.assertTrue(third["alert"])


class BacktestTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_backtest_hit_rate_and_feedback(self):
        for v in [0.010, 0.012] * 5:
            check("bt", {"value": v})
        check("bt", {"value": 0.05})
        result = backtest("bt")
        self.assertEqual(result["alerts"], 1)
        self.assertEqual(result["hit_rate"], 1.0)
        mark = feedback("bt")
        self.assertTrue(mark["false_positive"])
        result = backtest("bt")
        self.assertEqual(result["false_positives"], 1)
        self.assertEqual(result["hit_rate"], 0.0)

    def test_backtest_with_config_override(self):
        for v in [0.010, 0.012] * 5:
            check("bt2", {"value": v})
        check("bt2", {"value": 0.05})
        strict = backtest("bt2", config={"deviation_multiple": 100.0})
        self.assertEqual(strict["alerts"], 0)
        self.assertIsNone(strict["hit_rate"])


class TrendExportTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_direction_up(self):
        for v in (0.010, 0.012, 0.014, 0.016, 0.018, 0.020):
            check("up", {"value": v})
        self.assertEqual(trend("up")["direction"], "up")

    def test_export_includes_trend_and_backtest(self):
        for v in [0.010, 0.012] * 4:
            check("exp", {"value": v})
        data = export("exp")
        self.assertEqual(data["trend"]["direction"], "flat")
        self.assertEqual(len(data["series"]), 8)
        self.assertIn("backtest", data)

    def test_export_to_file(self):
        for v in (0.010, 0.012, 0.011):
            check("expf", {"value": v})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trend.json")
            export("expf", path=path)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        self.assertEqual(data["dataset"], "expf")
        self.assertEqual(len(data["series"]), 3)
