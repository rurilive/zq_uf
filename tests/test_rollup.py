import unittest

from metricrollup import add, query, reset


class RollupTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_natural_hour_bucket(self):
        add({"dim": "shop", "ts": 0, "value": 1})
        add({"dim": "shop", "ts": 3599, "value": 2})
        self.assertEqual(query("shop")["buckets"], 1)

    def test_late_data_new_bucket(self):
        add({"dim": "shop", "ts": 3600, "value": 1})
        add({"dim": "shop", "ts": 10, "value": 2})
        self.assertEqual(query("shop")["buckets"], 2)

    test_no_quantiles = property(lambda self: self.assertNotIn("p95", query("shop")))
