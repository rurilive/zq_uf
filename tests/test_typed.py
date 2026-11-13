import json
import unittest

from slapredict import (
    add_sample,
    backtest,
    dumps,
    from_dict,
    loads,
    outliers,
    predict,
    reset,
    to_dict,
)

ETL = [(1000, 158), (2000, 263), (3000, 357), (4000, 462), (5000, 558)]
REPORT = [(10000, 41), (50000, 119), (100000, 221)]


def seed():
    reset()
    for size, duration in ETL:
        add_sample("etl", size, duration)
    for size, duration in REPORT:
        add_sample("report", size, duration)


class FitTest(unittest.TestCase):
    def setUp(self):
        seed()

    def test_per_task_fit_differs(self):
        etl = predict("etl", 5000)
        report = predict("report", 5000)
        self.assertNotEqual(etl["eta"], report["eta"])
        self.assertAlmostEqual(etl["eta"], 558, delta=15)
        self.assertAlmostEqual(report["eta"], 30, delta=10)
        self.assertAlmostEqual(predict("report", 50000)["eta"], 120, delta=10)

    def test_eta_scales_with_size(self):
        small = predict("etl", 2000)["eta"]
        large = predict("etl", 4000)["eta"]
        self.assertGreater(large, small)
        self.assertAlmostEqual(large - small, 200, delta=30)

    def test_interval_contains_eta(self):
        result = predict("etl", 6000)
        self.assertLess(result["low"], result["eta"])
        self.assertLess(result["eta"], result["high"])

    def test_min_samples_boundary(self):
        reset()
        add_sample("etl", 100, 10)
        add_sample("etl", 200, 20)
        fallback = predict("etl", 100)
        self.assertEqual(fallback["eta"], fallback["low"])
        add_sample("etl", 300, 30)
        fitted = predict("etl", 100)
        self.assertAlmostEqual(fitted["eta"], 10.0, delta=1e-6)


class WeightTest(unittest.TestCase):
    def test_recent_samples_weigh_more(self):
        reset()
        for duration in (100, 100, 200):
            add_sample("etl", 100, duration)
        recent_heavy = predict("etl", 100)["eta"]
        reset()
        for duration in (200, 100, 100):
            add_sample("etl", 100, duration)
        older_heavy = predict("etl", 100)["eta"]
        self.assertGreater(recent_heavy, older_heavy)
        # 权重为 0.9^2, 0.9, 1，同 size 时退化为加权均值
        self.assertAlmostEqual(recent_heavy, (0.81 * 100 + 0.9 * 100 + 200) / 2.71)
        self.assertAlmostEqual(older_heavy, (0.81 * 200 + 0.9 * 100 + 100) / 2.71)


class OutlierTest(unittest.TestCase):
    def setUp(self):
        seed()
        add_sample("etl", 5000, 5000)

    def test_outlier_marked_and_excluded(self):
        marked = outliers("etl")
        self.assertEqual(len(marked), 1)
        self.assertEqual(marked[0]["duration"], 5000.0)
        result = predict("etl", 5000)
        self.assertEqual(result["outliers"], 1)
        self.assertEqual(result["n"], 5)
        self.assertAlmostEqual(result["eta"], 558, delta=15)

    def test_outliers_across_tasks(self):
        self.assertEqual(len(outliers()), 1)

    def test_no_false_positive_on_clean_data(self):
        reset()
        for size, duration in ETL:
            add_sample("etl", size, duration)
        self.assertEqual(outliers("etl"), [])


class BacktestTest(unittest.TestCase):
    def setUp(self):
        seed()

    def test_walk_forward(self):
        result = backtest("etl")
        self.assertEqual(result["count"], len(ETL) - 3)
        self.assertEqual(len(result["records"]), result["count"])
        self.assertGreaterEqual(result["mae"], 0)
        self.assertGreaterEqual(result["coverage"], 0.5)
        for record in result["records"]:
            self.assertEqual(
                record["hit"], record["low"] <= record["actual"] <= record["high"]
            )

    def test_backtest_unknown_task(self):
        with self.assertRaises(ValueError):
            backtest("nope")

    def test_backtest_insufficient_samples(self):
        with self.assertRaises(ValueError):
            backtest("report")


class StateTest(unittest.TestCase):
    def test_roundtrip_reproduces_prediction(self):
        seed()
        before = predict("etl", 6000)
        state = dumps()
        reset()
        self.assertNotEqual(predict("etl", 6000), before)
        loads(state)
        self.assertEqual(predict("etl", 6000), before)

    def test_to_dict_is_json_serializable(self):
        seed()
        json.dumps(to_dict())

    def test_from_dict_rejects_bad_state(self):
        with self.assertRaises(ValueError):
            from_dict({})
        with self.assertRaises(ValueError):
            loads("not json")


class ErrorTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_bad_size(self):
        for bad in (0, -1, "big", None, True):
            with self.assertRaises(ValueError):
                predict("etl", bad)

    def test_bad_task(self):
        for bad in ("", None, 123):
            with self.assertRaises(ValueError):
                predict(bad, 10)

    def test_bad_duration(self):
        with self.assertRaises(ValueError):
            add_sample("etl", 10, -1)
        with self.assertRaises(ValueError):
            reset([{"task": "etl", "size": 1, "duration": "x"}])

    def test_predict_without_any_samples(self):
        reset([])
        with self.assertRaises(ValueError):
            predict("etl", 10)


if __name__ == "__main__":
    unittest.main()
