import unittest

from driftcheck import THRESHOLD, check


class DriftTest(unittest.TestCase):
    def test_alert_over_fixed_threshold(self):
        self.assertTrue(check("orders", {"value": 130, "baseline": 100})["alert"])

    def test_no_alert_within_threshold(self):
        self.assertFalse(check("orders", {"value": 105, "baseline": 100})["alert"])

    def test_threshold_is_constant(self):
        self.assertEqual(THRESHOLD, 0.1)
        result = check("orders", {"value": 200, "baseline": 100})
        self.assertEqual(set(result.keys()), {"dataset", "alert", "drift"})
