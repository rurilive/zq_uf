import unittest

from metricroll3 import add, query


class RollTest(unittest.TestCase):
    def test_add_counts(self):
        add({"dim": "a", "window": "w1", "value": 2})
        self.assertEqual(add({"dim": "a", "window": "w1", "value": 3})["count"], 2)

    def test_sum(self):
        add({"dim": "b", "window": "w2", "value": 4})
        self.assertEqual(query("b", "w2")["sum"], 4.0)

    def test_p50_is_mean(self):
        add({"dim": "c", "window": "w3", "value": 1})
        add({"dim": "c", "window": "w3", "value": 3})
        self.assertEqual(query("c", "w3")["p50"], 2.0)
