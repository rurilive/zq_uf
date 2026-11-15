import unittest

from qtrend2 import check, trend


class TrendTest(unittest.TestCase):
    def test_fixed_threshold_alerts(self):
        self.assertTrue(check("d1", {"value": 0.5})["alert"])

    def test_low_value_quiet(self):
        self.assertFalse(check("d2", {"value": 0.01})["alert"])

    def test_no_direction(self):
        self.assertIsNone(trend("d3")["direction"])
