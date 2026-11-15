import unittest

from dnscache2 import report, resolve


class DnsTest(unittest.TestCase):
    def test_resolve_is_not_cached(self):
        self.assertFalse(resolve("shop")["cached"])

    def test_resolve_returns_address(self):
        self.assertEqual(resolve("shop")["ip"], "10.0.0.1")

    def test_report_does_not_refresh(self):
        self.assertFalse(report("shop", "10.0.0.1", True, 0.1)["refreshed"])
