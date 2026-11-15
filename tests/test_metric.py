import unittest

from metricmerge import add, recompute


class MetricTest(unittest.TestCase):
    def test_add_accumulates(self):
        add({"window": "w1", "value": 2})
        self.assertEqual(add({"window": "w1", "value": 3})["sum"], 5.0)

    def test_add_counts(self):
        add({"window": "w2", "value": 1})
        self.assertEqual(add({"window": "w2", "value": 1})["count"], 2)

    def test_recompute_does_nothing(self):
        add({"window": "w3", "value": 1})
        self.assertFalse(recompute("w3")["recomputed"])
