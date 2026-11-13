import unittest

from failcluster import record, report, reset


class ClusterTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_total_across_sites(self):
        record({"site": "shop", "error": "timeout"})
        record({"site": "news", "error": "timeout"})
        self.assertEqual(report()["total"], 2)

    def test_only_last_sample_kept(self):
        record({"site": "shop", "error": "timeout", "url": "/1"})
        record({"site": "news", "error": "parse", "url": "/2"})
        self.assertEqual(report()["last"]["url"], "/2")

    def test_no_clusters(self):
        record({"site": "shop", "error": "timeout"})
        self.assertNotIn("clusters", report())
