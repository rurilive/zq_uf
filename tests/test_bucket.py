import unittest
import threading
import time

from ratelimit import Limiter


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class RatelimitTest(unittest.TestCase):
    def test_first_request_does_not_sleep(self):
        clock = FakeClock()
        limiter = Limiter(interval=2.0, clock=clock, sleep=clock.sleep)
        limiter.acquire("a")
        self.assertEqual(clock.now, 0.0)

    def test_second_request_waits_between_interval(self):
        clock = FakeClock()
        limiter = Limiter(interval=2.0, clock=clock, sleep=clock.sleep)
        limiter.acquire("a")
        limiter.acquire("a")
        self.assertEqual(clock.now, 2.0)

    def test_sites_are_independent(self):
        clock = FakeClock()
        limiter = Limiter(interval=2.0, clock=clock, sleep=clock.sleep)
        limiter.acquire("a")
        limiter.acquire("b")
        self.assertEqual(clock.now, 0.0)
        self.assertEqual(set(limiter.last), {"a", "b"})

    def test_report_stored(self):
        limiter = Limiter(clock=FakeClock(), sleep=lambda seconds: None)
        limiter.report("a", 429)
        self.assertEqual(limiter.reports, [("a", 429)])


class AdaptiveTest(unittest.TestCase):
    def test_backoff_on_429_and_5xx(self):
        clock = FakeClock()
        limiter = Limiter(interval=1.0, clock=clock, sleep=clock.sleep)
        limiter.acquire("a")
        limiter.report("a", 429)
        self.assertEqual(limiter.stats("a")["interval"], 2.0)
        limiter.report("a", 503)
        self.assertEqual(limiter.stats("a")["interval"], 4.0)

    def test_backoff_slows_acquire(self):
        clock = FakeClock()
        limiter = Limiter(interval=1.0, clock=clock, sleep=clock.sleep)
        limiter.acquire("a")
        limiter.report("a", 429)
        limiter.acquire("a")
        self.assertEqual(clock.now, 2.0)

    def test_backoff_capped_by_max_interval(self):
        clock = FakeClock()
        limiter = Limiter(interval=1.0, max_interval=5.0,
                          clock=clock, sleep=clock.sleep)
        for _ in range(10):
            limiter.report("a", 429)
        self.assertEqual(limiter.stats("a")["interval"], 5.0)

    def test_recovery_after_consecutive_successes(self):
        clock = FakeClock()
        limiter = Limiter(interval=1.0, recover_after=3,
                          clock=clock, sleep=clock.sleep)
        limiter.report("a", 429)
        limiter.report("a", 429)
        self.assertEqual(limiter.stats("a")["interval"], 4.0)
        for _ in range(2):
            limiter.report("a", 200)
        self.assertEqual(limiter.stats("a")["interval"], 4.0)
        limiter.report("a", 200)
        self.assertAlmostEqual(limiter.stats("a")["interval"], 4.0 / 1.5)

    def test_recovery_never_faster_than_base_interval(self):
        clock = FakeClock()
        limiter = Limiter(interval=1.0, recover_after=2,
                          clock=clock, sleep=clock.sleep)
        limiter.report("a", 429)
        for _ in range(50):
            limiter.report("a", 200)
        self.assertEqual(limiter.stats("a")["interval"], 1.0)

    def test_failure_resets_success_streak(self):
        clock = FakeClock()
        limiter = Limiter(interval=1.0, recover_after=2,
                          clock=clock, sleep=clock.sleep)
        limiter.report("a", 429)
        limiter.report("a", 200)
        limiter.report("a", 500)
        limiter.report("a", 200)
        self.assertEqual(limiter.stats("a")["interval"], 4.0)


class SiteIsolationTest(unittest.TestCase):
    def test_backoff_isolated_per_site(self):
        clock = FakeClock()
        limiter = Limiter(interval=1.0, clock=clock, sleep=clock.sleep)
        limiter.acquire("a")
        limiter.acquire("b")
        limiter.report("a", 429)
        limiter.acquire("b")
        self.assertEqual(clock.now, 1.0)
        self.assertEqual(limiter.stats("b")["interval"], 1.0)
        limiter.acquire("a")
        self.assertEqual(clock.now, 2.0)

    def test_per_site_config(self):
        clock = FakeClock()
        limiter = Limiter(interval=1.0,
                          sites={"slow": {"interval": 4.0, "burst": 1}},
                          clock=clock, sleep=clock.sleep)
        limiter.acquire("slow")
        limiter.acquire("slow")
        self.assertEqual(clock.now, 4.0)
        limiter.acquire("fast")
        limiter.acquire("fast")
        self.assertEqual(clock.now, 5.0)

    def test_configure_at_runtime(self):
        clock = FakeClock()
        limiter = Limiter(interval=1.0, clock=clock, sleep=clock.sleep)
        limiter.configure("a", interval=3.0, burst=2)
        limiter.acquire("a")
        limiter.acquire("a")
        self.assertEqual(clock.now, 0.0)
        limiter.acquire("a")
        self.assertEqual(clock.now, 3.0)

    def test_burst_allows_immediate_requests(self):
        clock = FakeClock()
        limiter = Limiter(interval=1.0, burst=3, clock=clock, sleep=clock.sleep)
        for _ in range(3):
            limiter.acquire("a")
        self.assertEqual(clock.now, 0.0)
        limiter.acquire("a")
        self.assertEqual(clock.now, 1.0)


class StatsTest(unittest.TestCase):
    def test_stats_track_wait_and_throttled(self):
        clock = FakeClock()
        limiter = Limiter(interval=1.0, clock=clock, sleep=clock.sleep)
        limiter.acquire("a")
        limiter.acquire("a")
        limiter.acquire("a")
        limiter.report("a", 429)
        limiter.report("a", 503)
        limiter.report("a", 200)
        stats = limiter.stats("a")
        self.assertEqual(stats["requests"], 3)
        self.assertEqual(stats["wait_total"], 2.0)
        self.assertEqual(stats["throttled"], 2)

    def test_stats_all_sites(self):
        clock = FakeClock()
        limiter = Limiter(interval=1.0, clock=clock, sleep=clock.sleep)
        limiter.acquire("a")
        limiter.acquire("b")
        all_stats = limiter.stats()
        self.assertEqual(set(all_stats), {"a", "b"})
        self.assertEqual(all_stats["a"]["requests"], 1)
        self.assertEqual(all_stats["b"]["wait_total"], 0.0)


class ThreadedTest(unittest.TestCase):
    def test_threads_share_bucket_and_respect_rate(self):
        limiter = Limiter(interval=0.03, burst=1)
        threads = [
            threading.Thread(
                target=lambda: [limiter.acquire("a") for _ in range(4)])
            for _ in range(4)
        ]
        start = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        elapsed = time.monotonic() - start
        # 16 个请求、容量 1，至少等待 15 个间隔（留调度余量）
        self.assertGreaterEqual(elapsed, 0.03 * 15 * 0.8)
        stats = limiter.stats("a")
        self.assertEqual(stats["requests"], 16)
        self.assertGreater(stats["wait_total"], 0.0)

    def test_threads_on_separate_sites_do_not_block_each_other(self):
        limiter = Limiter(interval=0.03, burst=1)
        threads = [
            threading.Thread(
                target=lambda site=site: [limiter.acquire(site)
                                          for _ in range(4)])
            for site in ("a", "b", "c", "d")
        ]
        start = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        elapsed = time.monotonic() - start
        # 各站点独立：4 个站点并行，总耗时应接近单站点的 3 个间隔
        self.assertLess(elapsed, 0.03 * 15 * 0.8)
