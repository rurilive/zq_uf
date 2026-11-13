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

    def test_same_root_cause_merges(self):
        for i in range(5):
            record({"site": "shop", "error": "timeout",
                    "selector": "#price", "task": "t%d" % i, "ts": 100 + i})
        clusters = report()["clusters"]
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["count"], 5)
        self.assertEqual(clusters[0]["affected_tasks"], 5)

    def test_distinct_stacks_not_merged(self):
        record({"site": "shop", "error": "crash", "ts": 1,
                "stack": 'File "a.py", line 1, in fetch'})
        record({"site": "shop", "error": "crash", "ts": 2,
                "stack": 'File "b.py", line 9, in parse'})
        self.assertEqual(len(report()["clusters"]), 2)

    def test_clusters_sorted_by_impact(self):
        for i in range(3):
            record({"site": "shop", "error": "timeout",
                    "task": "t%d" % i, "ts": 10 + i})
        record({"site": "news", "error": "parse", "task": "n1", "ts": 100})
        record({"site": "news", "error": "parse", "task": "n1", "ts": 200})
        clusters = report()["clusters"]
        self.assertEqual(clusters[0]["key"]["site"], "shop")
        self.assertEqual(clusters[0]["affected_tasks"], 3)
        self.assertEqual(clusters[1]["affected_tasks"], 1)

    def test_sporadic_vs_persistent(self):
        record({"site": "shop", "error": "timeout", "ts": 1000})
        record({"site": "shop", "error": "timeout", "ts": 1010})
        for i in range(4):
            record({"site": "news", "error": "parse", "ts": 1000 + i * 60})
        categories = {c["key"]["error"]: c["category"]
                      for c in report()["clusters"]}
        self.assertEqual(categories["timeout"], "sporadic")
        self.assertEqual(categories["parse"], "persistent")

    def test_regression_after_gap(self):
        for ts in (1000, 1100, 1200, 10000, 10100):
            record({"site": "shop", "error": "rule", "ts": ts})
        categories = [c["category"] for c in report()["clusters"]]
        self.assertEqual(categories, ["regression"])

    def test_samples_and_time_range_kept(self):
        for i in range(5):
            record({"site": "shop", "error": "timeout",
                    "url": "/%d" % i, "ts": 100 + i})
        cluster = report()["clusters"][0]
        self.assertEqual(len(cluster["samples"]), 3)
        self.assertEqual(cluster["samples"][0]["url"], "/0")
        self.assertEqual(cluster["samples"][-1]["url"], "/4")
        self.assertEqual(cluster["first_seen"], 100)
        self.assertEqual(cluster["last_seen"], 104)
        self.assertEqual(cluster["duration"], 4)

    def test_time_window(self):
        record({"site": "shop", "error": "timeout", "ts": 100})
        record({"site": "news", "error": "parse", "ts": 200})
        record({"site": "blog", "error": "rule", "ts": 300})
        windowed = report(since=150, until=250)
        self.assertEqual(windowed["total"], 1)
        self.assertEqual(windowed["last"]["site"], "news")
        self.assertNotIn("clusters", windowed)
        full = report(since=100)
        self.assertEqual(full["total"], 3)
        self.assertEqual(len(full["clusters"]), 3)
