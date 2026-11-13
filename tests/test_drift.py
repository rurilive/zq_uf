import unittest

from driftcheck import THRESHOLD, backtest, check, merge_alerts


class DriftTest(unittest.TestCase):
    def test_alert_over_fixed_threshold(self):
        self.assertTrue(check("orders", {"value": 130, "baseline": 100})["alert"])

    def test_no_alert_within_threshold(self):
        self.assertFalse(check("orders", {"value": 105, "baseline": 100})["alert"])

    def test_threshold_is_constant(self):
        self.assertEqual(THRESHOLD, 0.1)
        result = check("orders", {"value": 200, "baseline": 100})
        self.assertEqual(set(result.keys()), {"dataset", "alert", "drift"})


class BaselineTest(unittest.TestCase):
    def test_baseline_from_recent_window(self):
        history = [200, 200, 98, 102, 100, 104, 96, 100, 100]
        result = check("orders", {"value": 100, "history": history})
        self.assertEqual(result["baseline"], 100.0)  # 最近 7 天均值
        self.assertEqual(result["window"], 7)
        self.assertFalse(result["alert"])

    def test_alert_carries_context(self):
        history = [100] * 10
        result = check("orders", {"value": 150, "history": history})
        self.assertTrue(result["alert"])
        self.assertEqual(result["value"], 150)
        self.assertEqual(result["deviation"], 0.5)
        self.assertIn("window drift", result["reasons"])

    def test_window_configurable(self):
        history = [10, 10, 10, 10, 20, 20, 20, 20]
        metric = {"value": 20, "history": history, "config": {"window": 4}}
        self.assertEqual(check("orders", metric)["baseline"], 20.0)


class PeriodTest(unittest.TestCase):
    def setUp(self):
        # 周末高峰约 200、工作日约 100 的周周期序列
        self.weekly = [100, 102, 98, 101, 99, 200, 205] * 3 + [100, 102, 98, 101, 99]

    def test_weekend_peak_matches_same_period(self):
        result = check("orders", {"value": 203, "history": self.weekly})
        self.assertFalse(result["alert"])
        self.assertAlmostEqual(result["yoy"], 0.0, delta=0.05)

    def test_weekend_anomaly_alerts_period_drift(self):
        result = check("orders", {"value": 320, "history": self.weekly})
        self.assertTrue(result["alert"])
        self.assertIn("period drift", result["reasons"])

    def test_explicit_same_period_samples(self):
        metric = {"value": 100, "history": [100] * 10, "same_period": [98, 102, 100]}
        result = check("orders", metric)
        self.assertAlmostEqual(result["yoy"], 0.0, delta=0.03)

    def test_wow_ratio_against_previous_day(self):
        result = check("orders", {"value": 110, "history": [100] * 10})
        self.assertEqual(result["wow"], 0.1)


class TrendTest(unittest.TestCase):
    def test_slow_drift_detected_by_slope(self):
        history = [round(100 * 1.03 ** i, 2) for i in range(14)]
        result = check("orders", {"value": round(100 * 1.03 ** 14, 2), "history": history})
        self.assertTrue(result["alert"])
        self.assertIn("trend drift", result["reasons"])
        self.assertGreater(result["trend_slope"], 0.02)

    def test_stable_series_no_trend(self):
        result = check("orders", {"value": 101, "history": [100, 101, 99, 100, 101, 99, 100, 101]})
        self.assertNotIn("trend drift", result["reasons"])


class MergeTest(unittest.TestCase):
    def _alert(self, reasons, date):
        return {"dataset": "orders", "alert": True, "reasons": reasons, "date": date}

    def test_consecutive_duplicates_merged(self):
        alerts = [self._alert(["window drift"], d) for d in (1, 2, 3)]
        merged = merge_alerts(alerts)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["count"], 3)
        self.assertEqual(merged[0]["first_date"], 1)
        self.assertEqual(merged[0]["last_date"], 3)

    def test_different_reasons_not_merged(self):
        alerts = [self._alert(["window drift"], 1), self._alert(["trend drift"], 2)]
        self.assertEqual(len(merge_alerts(alerts)), 2)

    def test_non_consecutive_not_merged(self):
        alerts = [
            self._alert(["window drift"], 1),
            self._alert(["trend drift"], 2),
            self._alert(["window drift"], 3),
        ]
        self.assertEqual(len(merge_alerts(alerts)), 3)


class BacktestTest(unittest.TestCase):
    def test_stable_series_quiet(self):
        series = [100 + (i % 3) - 1 for i in range(40)]
        result = backtest("orders", series)
        self.assertEqual(result["days"], 32)
        self.assertEqual(result["alerts"], 0)
        self.assertEqual(result["alert_rate"], 0.0)

    def test_spike_detected_in_backtest(self):
        series = [100] * 20 + [200] + [100] * 19
        result = backtest("orders", series)
        self.assertGreaterEqual(result["alerts"], 1)
        self.assertTrue(any("window drift" in m["reasons"] for m in result["merged"]))

    def test_backtest_merges_consecutive_alerts(self):
        series = [100] * 20 + [200, 200] + [100] * 18
        result = backtest("orders", series)
        counts = [m["count"] for m in result["merged"]]
        self.assertEqual(sum(counts), result["alerts"])
