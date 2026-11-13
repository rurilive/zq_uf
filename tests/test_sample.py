import random
import unittest

from sampler import sample, summary


ROWS = [{"id": index, "value": index} for index in range(100)]


class SampleTest(unittest.TestCase):
    def test_size_capped(self):
        self.assertEqual(len(sample(ROWS, {"n": 5})), 5)
        self.assertEqual(len(sample(ROWS, {"n": 500})), 100)

    def test_plan_seed_ignored(self):
        random.seed(7)
        expected = random.sample(ROWS, 5)
        random.seed(7)
        self.assertEqual(sample(ROWS, {"n": 5, "seed": 999}), expected)

    def test_no_strata(self):
        rows = [{"id": 1, "city": "sh", "value": 1}, {"id": 2, "city": "bj", "value": 2}]
        self.assertEqual({row["city"] for row in sample(rows, {"n": 2, "strata": "city"})}, {"sh", "bj"})

    def test_summary_mean_only(self):
        self.assertEqual(set(summary([{"value": 1}, {"value": 3}]).keys()), {"count", "mean"})
