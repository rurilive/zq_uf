import unittest

from crawlcap import acquire, report


class CapTest(unittest.TestCase):
    def test_fixed_limit(self):
        for _ in range(4):
            self.assertTrue(acquire("shop")["ok"])
        self.assertFalse(acquire("shop")["ok"])

    def test_limits_are_per_site(self):
        for _ in range(4):
            acquire("news")
        self.assertTrue(acquire("forum")["ok"])

    def test_report_keeps_concurrency(self):
        acquire("blog")
        self.assertEqual(report("blog", True, 0.2)["concurrency"], 1)
