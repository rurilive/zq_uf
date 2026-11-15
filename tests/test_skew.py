import unittest

from skewwatch import observe, report


class SkewTest(unittest.TestCase):
    def test_observe_never_skewed(self):
        self.assertFalse(observe({"name": "t1", "elapsed": 100})["skewed"])

    def test_report_ratio_one(self):
        self.assertEqual(report("t1")["ratio"], 1.0)

    def test_no_hot_keys(self):
        self.assertEqual(report("t1")["hot"], [])
