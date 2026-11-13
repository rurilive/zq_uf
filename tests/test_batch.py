import unittest

from detailcrawl import crawl
from detailcrawl.crawl import BatchStats, CrawlResult


class BatchStatsTest(unittest.TestCase):
    def fetch(self, url):
        if url.endswith("/skip"):
            return None
        if url.endswith("/timeout"):
            raise TimeoutError("boom")
        if url.endswith("/error"):
            raise ValueError("bad")
        return "ok-" + url.rsplit("/", 1)[1]

    def test_results_align_with_input_order(self):
        urls = [
            "http://s/ok1", "http://s/ok2",
            "http://s/skip", "http://s/timeout", "http://s/error",
        ]
        result = crawl(urls, self.fetch, retries=2, backoff=0)
        self.assertIsInstance(result, CrawlResult)
        self.assertEqual(result, ["ok-ok1", "ok-ok2", None, None, None])

    def test_batch_stats_buckets_and_attempts(self):
        urls = [
            "http://s/ok1", "http://s/ok2",
            "http://s/skip", "http://s/timeout", "http://s/error",
        ]
        stats = crawl(urls, self.fetch, retries=2, backoff=0).stats
        self.assertIsInstance(stats, BatchStats)
        self.assertEqual(stats.total, 5)
        self.assertEqual(len(stats.succeeded), 2)
        self.assertEqual(len(stats.skipped), 1)
        self.assertEqual(len(stats.failed), 2)
        timeout_rec = next(r for r in stats.failed if "timeout" in r.url)
        error_rec = next(r for r in stats.failed if "error" in r.url)
        self.assertEqual(timeout_rec.attempts, 3)
        self.assertEqual(error_rec.attempts, 1)
        self.assertEqual(stats.skipped[0].attempts, 1)

    def test_phase_timings_and_summary(self):
        urls = ["http://s/ok1", "http://s/skip", "http://s/error"]
        stats = crawl(urls, self.fetch, backoff=0).stats
        self.assertGreaterEqual(stats.fetch_seconds, 0)
        self.assertGreaterEqual(stats.parse_seconds, 0)
        self.assertGreater(stats.wall_seconds, 0)
        summary = stats.summary()
        self.assertIn("total=3", summary)
        self.assertIn("ok=1", summary)
        self.assertIn("skipped=1", summary)
        self.assertIn("failed=1", summary)
        self.assertIn("wall=", summary)
        details = stats.details()
        self.assertIn("[skipped] http://s/skip", details)
        self.assertIn("[failed] http://s/error", details)


if __name__ == "__main__":
    unittest.main()
