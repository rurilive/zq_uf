import unittest

from crawlmetrics import Stats


class CrawlmetricsTest(unittest.TestCase):
    def test_inc_accumulates(self):
        stats = Stats()
        stats.inc("success")
        stats.inc("success", 2)
        self.assertEqual(stats.snapshot()["success"], 3)

    def test_snapshot_is_copy(self):
        stats = Stats()
        stats.inc("fail")
        snapshot = stats.snapshot()
        snapshot["fail"] = 99
        self.assertEqual(stats.snapshot()["fail"], 1)

    def test_empty_snapshot(self):
        self.assertEqual(Stats().snapshot(), {})
