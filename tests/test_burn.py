import unittest

from proxyburn import report, use


class BurnTest(unittest.TestCase):
    def test_use_is_always_open(self):
        self.assertTrue(use("p1")["usable"])

    def test_report_never_trips(self):
        self.assertFalse(report("p1", False)["tripped"])

    def test_state_is_closed(self):
        self.assertEqual(use("p1")["state"], "closed")
