import json
import random
import unittest

import demo
from sampler import replay, sample, summary


def city_rows(sizes):
    rows = []
    for city, count in sizes.items():
        for index in range(count):
            rows.append({"id": len(rows), "city": city, "value": len(rows)})
    return rows


class StratifiedSampleTest(unittest.TestCase):
    def test_fixed_seed_reproducible(self):
        rows = city_rows({"a": 60, "b": 40})
        plan = {"strata": "city", "n": 10, "random_seed": 7}
        random.seed(12345)
        first = sample(rows, plan)
        random.seed(999)
        second = sample(rows, plan)
        self.assertEqual(first, second)

    def test_different_seed_differs(self):
        rows = city_rows({"a": 60, "b": 40})
        one = sample(rows, {"strata": "city", "n": 10, "random_seed": 1})
        two = sample(rows, {"strata": "city", "n": 10, "random_seed": 2})
        self.assertNotEqual(one, two)

    def test_fixed_n_proportional_allocation(self):
        rows = city_rows({"a": 80, "b": 20})
        picked = sample(rows, {"strata": "city", "n": 10, "random_seed": 1})
        self.assertEqual(len(picked), 10)
        self.assertEqual(picked.strata_counts, {"a": 8, "b": 2})

    def test_fixed_n_long_tail_kept(self):
        rows = city_rows({"a": 97, "b": 2, "c": 1})
        picked = sample(rows, {"strata": "city", "n": 10, "random_seed": 1})
        self.assertEqual(picked.strata_counts, {"a": 8, "b": 1, "c": 1})

    def test_ratio_per_stratum(self):
        rows = city_rows({"a": 100, "b": 100})
        picked = sample(rows, {"strata": "city", "ratio": 0.1, "random_seed": 1})
        self.assertEqual(picked.strata_counts, {"a": 10, "b": 10})

    def test_ratio_one_returns_all(self):
        rows = city_rows({"a": 30, "b": 20})
        picked = sample(rows, {"strata": "city", "ratio": 1, "random_seed": 1})
        self.assertEqual(len(picked), 50)

    def test_ratio_without_strata(self):
        rows = [{"id": i, "value": i} for i in range(100)]
        picked = sample(rows, {"ratio": 0.2, "random_seed": 1})
        self.assertEqual(len(picked), 20)

    def test_default_n(self):
        rows = [{"id": i, "value": i} for i in range(100)]
        self.assertEqual(len(sample(rows, {})), 10)

    def test_state_serializable_and_replay(self):
        rows = city_rows({"a": 60, "b": 40})
        picked = sample(rows, {"strata": "city", "n": 10, "random_seed": 7})
        state = json.loads(json.dumps(picked.to_state()))
        self.assertEqual(replay(rows, state), picked)
        self.assertEqual(sample(rows, state["plan"]), picked)


class PlanValidationTest(unittest.TestCase):
    def test_n_and_ratio_conflict(self):
        with self.assertRaises(ValueError):
            sample([{"value": 1}], {"n": 1, "ratio": 0.5})

    def test_bad_ratio(self):
        for bad in (0, -0.1, 1.5, "0.5", True):
            with self.assertRaises(ValueError):
                sample([{"value": 1}], {"ratio": bad})

    def test_bad_n(self):
        for bad in (0, -3, 2.5, "5", True):
            with self.assertRaises(ValueError):
                sample([{"value": 1}], {"n": bad})

    def test_plan_not_dict(self):
        with self.assertRaises(TypeError):
            sample([{"value": 1}], None)

    def test_missing_strata_field(self):
        rows = [{"city": "a", "value": 1}, {"value": 2}]
        with self.assertRaises(ValueError):
            sample(rows, {"strata": "city", "n": 1, "random_seed": 1})

    def test_non_dict_row(self):
        with self.assertRaises(TypeError):
            sample([{"city": "a"}, ("b", 1)], {"strata": "city", "n": 1})

    def test_replay_bad_state(self):
        with self.assertRaises(ValueError):
            replay([{"value": 1}], {})


class SummaryTest(unittest.TestCase):
    def test_quantiles_and_error(self):
        result = summary([{"value": i} for i in range(1, 101)])
        self.assertEqual(result["count"], 100)
        self.assertAlmostEqual(result["mean"], 50.5)
        self.assertAlmostEqual(result.quantiles["p50"], 50.5)
        self.assertAlmostEqual(result.quantiles["p90"], 90.1)
        self.assertAlmostEqual(result.quantiles["p99"], 99.01)
        expected = 1.96 * ((100 * 101 / 12) / 100) ** 0.5
        self.assertAlmostEqual(result.error, expected)

    def test_empty(self):
        result = summary([])
        self.assertEqual(result["count"], 0)
        self.assertIsNone(result["mean"])
        self.assertIsNone(result.quantiles)
        self.assertIsNone(result.error)

    def test_single_row(self):
        result = summary([{"value": 5}])
        self.assertEqual(result.quantiles, {"p50": 5, "p90": 5, "p99": 5})
        self.assertIsNone(result.error)

    def test_to_dict_serializable(self):
        result = summary([{"value": 1}, {"value": 3}])
        data = json.loads(json.dumps(result.to_dict()))
        self.assertEqual(data["mean"], 2)
        self.assertEqual(set(data["quantiles"]), {"p50", "p90", "p99"})

    def test_missing_value_field(self):
        with self.assertRaises(ValueError):
            summary([{"value": 1}, {"other": 2}])

    def test_non_numeric_value(self):
        with self.assertRaises(ValueError):
            summary([{"value": "x"}])


class DemoTest(unittest.TestCase):
    def test_demo_plan_output(self):
        rows = demo.build_rows()
        picked = sample(rows, demo.PLAN)
        self.assertEqual(len(picked), 51)
        self.assertEqual(picked.strata_counts, {"bj": 45, "sh": 5, "sz": 1})
        self.assertEqual(picked, sample(rows, demo.PLAN))
        part = summary(picked)
        self.assertEqual(part["count"], 51)
        self.assertTrue(any(row["value"] >= 100000 for row in picked))


if __name__ == "__main__":
    unittest.main()
