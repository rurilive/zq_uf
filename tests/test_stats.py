import json
import os
import tempfile
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

    def test_stage_counter_rollup(self):
        stats = Stats()
        stats.inc("request.success", site="a.com", task="crawl-a")
        stats.inc("request.success", site="a.com", task="crawl-b")
        stats.inc("request.fail", site="a.com", task="crawl-a")
        stats.inc("request.retry", site="b.com", task="crawl-c")
        stats.inc("parse.success", site="a.com", task="crawl-a")
        stats.inc("storage.fail", site="b.com", task="crawl-c")

        counters = stats.snapshot()["counters"]
        self.assertEqual(counters["request.success"]["total"], 2)
        self.assertEqual(counters["request.success"]["sites"]["a.com"], 2)
        self.assertEqual(counters["request.success"]["tasks"]["crawl-a"], 1)
        self.assertEqual(counters["request.success"]["tasks"]["crawl-b"], 1)
        self.assertEqual(counters["request.fail"]["sites"], {"a.com": 1})
        self.assertEqual(counters["request.retry"]["sites"], {"b.com": 1})
        self.assertEqual(counters["parse.success"]["total"], 1)
        self.assertEqual(counters["storage.fail"]["total"], 1)

    def test_counter_requires_dimension(self):
        stats = Stats()
        with self.assertRaises(ValueError):
            stats.inc("request.success")
        with self.assertRaises(ValueError):
            stats.inc("request.unknown", site="a.com")
        with self.assertRaises(ValueError):
            stats.inc("duration.unknown", 0.1)
        with self.assertRaises(ValueError):
            stats.inc("legacy", site="a.com")

    def test_timer_quantiles_and_rollup(self):
        stats = Stats()
        values = [0.1, 0.1, 0.1, 0.2, 0.2, 0.5, 1.0, 1.0, 2.0, 5.0]
        for index, value in enumerate(values):
            site = "a.com" if index < 7 else "b.com"
            stats.timing("request", value, site=site, task="crawl-a")

        timer = stats.snapshot()["timers"]["request"]
        self.assertEqual(timer["count"], 10)
        self.assertEqual(timer["min"], 0.1)
        self.assertEqual(timer["max"], 5.0)
        self.assertAlmostEqual(timer["sum"], sum(values))
        self.assertAlmostEqual(timer["mean"], sum(values) / 10)
        quantiles = timer["quantiles"]
        self.assertEqual(set(quantiles), {"p50", "p90", "p95", "p99"})
        self.assertLessEqual(quantiles["p50"], quantiles["p90"])
        self.assertLessEqual(quantiles["p90"], quantiles["p95"])
        self.assertLessEqual(quantiles["p95"], quantiles["p99"])
        self.assertTrue(0.2 <= quantiles["p50"] <= 0.3)
        self.assertTrue(2.0 <= quantiles["p90"] <= 3.0)
        self.assertTrue(5.0 <= quantiles["p99"] <= 10.0)
        self.assertEqual(timer["sites"]["a.com"]["count"], 7)
        self.assertEqual(timer["sites"]["b.com"]["count"], 3)
        self.assertEqual(timer["tasks"]["crawl-a"]["count"], 10)
        self.assertEqual(sum(timer["histogram"]["counts"]), 10)

    def test_timer_via_inc_duration(self):
        stats = Stats()
        stats.inc("duration.parse", 0.25, site="a.com", task="crawl-a")
        timer = stats.snapshot()["timers"]["parse"]
        self.assertEqual(timer["count"], 1)
        self.assertAlmostEqual(timer["max"], 0.25)

    def test_snapshot_json_serializable_and_isolated(self):
        stats = Stats()
        stats.inc("legacy", 3)
        stats.inc("request.success", site="a.com", task="crawl-a")
        stats.timing("request", 0.1, site="a.com", task="crawl-a")

        snapshot = stats.snapshot()
        encoded = json.dumps(snapshot)
        restored = Stats(json.loads(encoded))
        self.assertEqual(restored.snapshot(), snapshot)

        snapshot["counters"]["request.success"]["total"] = 99
        snapshot["timers"]["request"]["count"] = 99
        fresh = stats.snapshot()
        self.assertEqual(fresh["counters"]["request.success"]["total"], 1)
        self.assertEqual(fresh["timers"]["request"]["count"], 1)

    def test_merge_process_snapshots(self):
        worker_one = Stats()
        worker_one.inc("legacy", 2)
        worker_one.inc("request.success", site="a.com", task="crawl-a")
        worker_one.inc("request.fail", site="a.com", task="crawl-a")
        worker_one.timing("request", 0.1, site="a.com", task="crawl-a")
        worker_one.timing("storage", 0.5, site="a.com", task="crawl-a")

        worker_two = Stats()
        worker_two.inc("legacy", 5)
        worker_two.inc("request.success", site="b.com", task="crawl-b")
        worker_two.timing("request", 0.2, site="b.com", task="crawl-b")

        merged = Stats.merge_snapshots(
            [worker_one.snapshot(), worker_two.snapshot()]
        )
        self.assertEqual(merged["legacy"], 7)
        request = merged["counters"]["request.success"]
        self.assertEqual(request["total"], 2)
        self.assertEqual(request["sites"], {"a.com": 1, "b.com": 1})
        self.assertEqual(merged["counters"]["request.fail"]["total"], 1)
        timer = merged["timers"]["request"]
        self.assertEqual(timer["count"], 2)
        self.assertEqual(timer["min"], 0.1)
        self.assertEqual(timer["max"], 0.2)
        self.assertEqual(timer["sites"]["a.com"]["count"], 1)
        self.assertEqual(timer["sites"]["b.com"]["count"], 1)
        self.assertEqual(merged["timers"]["storage"]["count"], 1)

        accumulator = Stats()
        self.assertIs(accumulator.merge(worker_one.snapshot()), accumulator)
        accumulator.merge(worker_two.snapshot())
        self.assertEqual(accumulator.snapshot(), merged)

    def test_restart_resumes_from_dump(self):
        stats = Stats()
        stats.inc("legacy", 4)
        stats.inc("request.success", site="a.com", task="crawl-a")
        stats.inc("request.retry", site="a.com", task="crawl-a")
        stats.timing("request", 0.1, site="a.com", task="crawl-a")

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "stats.json")
            stats.dump(path)

            restarted = Stats.load(path)
            restarted.inc("legacy", 6)
            restarted.inc("request.success", site="a.com", task="crawl-b")
            restarted.timing("request", 0.3, site="a.com", task="crawl-b")

        snapshot = restarted.snapshot()
        self.assertEqual(snapshot["legacy"], 10)
        self.assertEqual(snapshot["counters"]["request.success"]["total"], 2)
        self.assertEqual(snapshot["counters"]["request.retry"]["total"], 1)
        timer = snapshot["timers"]["request"]
        self.assertEqual(timer["count"], 2)
        self.assertEqual(timer["min"], 0.1)
        self.assertEqual(timer["max"], 0.3)
        self.assertEqual(timer["sites"]["a.com"]["count"], 2)

    def test_incompatible_histogram_rejected(self):
        stats = Stats()
        stats.timing("request", 0.1, site="a.com", task="crawl-a")
        bad = stats.snapshot()
        bad["timers"]["request"]["histogram"]["bins"] = [1, 2, 3]
        with self.assertRaises(ValueError):
            Stats(bad)
