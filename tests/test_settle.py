import unittest

from proxysettle import charge, settle


class SettleTest(unittest.TestCase):
    def test_charge_accumulates(self):
        charge("p1", 1.5)
        self.assertEqual(charge("p1", 2.5)["balance"], 4.0)

    def test_failed_request_also_charged(self):
        charge("p2", 1.0)
        charge("p2", 1.0)
        self.assertEqual(settle("p2")["balance"], 2.0)

    def test_settle_has_no_diff(self):
        self.assertEqual(settle("p3")["diff"], 0.0)
