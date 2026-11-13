import unittest

from aggretract import Aggregator


class AggretractTest(unittest.TestCase):
    def test_apply_adds(self):
        agg = Aggregator()
        agg.apply([{"key": "a", "value": 2}])
        agg.apply([{"key": "a", "value": 3}])
        self.assertEqual(agg.query("a"), 5)

    def test_unknown_key_is_zero(self):
        self.assertEqual(Aggregator().query("x"), 0)

    def test_multiple_keys(self):
        agg = Aggregator()
        agg.apply([{"key": "a", "value": 1}, {"key": "b", "value": 2}])
        self.assertEqual(agg.totals, {"a": 1, "b": 2})
