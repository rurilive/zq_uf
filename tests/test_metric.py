import unittest

from metriclate2 import add, recompute


class MetricTest(unittest.TestCase):
    def test_add_accumulates(self):
        add({"window": "w1", "value": 2})
        self.assertEqual(add({"window": "w1", "value": 3})["sum"], 5.0)

    def test_recompute_is_read_only(self):
        add({"window": "w2", "value": 1})
        self.assertFalse(recompute("w2")["recomputed"])

    def test_no_retraction(self):
        self.assertIsNone(recompute("w2")["retracted"])
