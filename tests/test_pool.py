import random
import unittest

from proxysticky import PROXIES, acquire


class PoolTest(unittest.TestCase):
    def test_returns_configured_proxy(self):
        self.assertIn(acquire("shop", "s1"), PROXIES)

    def test_same_session_can_switch(self):
        random.seed(3)
        picked = {acquire("shop", "s1") for _ in range(20)}
        self.assertGreater(len(picked), 1)

    def test_sites_share_pool(self):
        random.seed(5)
        left = {acquire("shop", "s1") for _ in range(20)}
        right = {acquire("news", "s2") for _ in range(20)}
        self.assertTrue(left & right)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_pool(proxies, **kwargs):
    from proxysticky import StickyPool
    kwargs.setdefault("time_func", FakeClock())
    kwargs.setdefault("rng", random.Random(7))
    return StickyPool(proxies, **kwargs)


class StickyPoolTest(unittest.TestCase):
    def test_sticky_hold_within_window(self):
        pool = make_pool(["p1", "p2", "p3"], sticky_window=30)
        first = pool.acquire("shop", "s1")
        for _ in range(10):
            self.assertEqual(pool.acquire("shop", "s1"), first)
        bound = pool.bindings()
        self.assertEqual(bound["shop"]["s1"]["proxy"], first)
        self.assertEqual(bound["shop"]["s1"]["reason"], "new-session")

    def test_expired_session_reselects(self):
        clock = FakeClock()
        pool = make_pool(["p1"], sticky_window=30, time_func=clock)
        self.assertEqual(pool.acquire("shop", "s1"), "p1")
        clock.advance(31)
        self.assertEqual(pool.acquire("shop", "s1"), "p1")
        self.assertEqual(pool.switch_log()[-1]["reason"], "expired")

    def test_failed_exit_switches_after_threshold(self):
        pool = make_pool(["p1", "p2"], sticky_window=30, failure_threshold=2)
        bound = pool.acquire("shop", "s1")
        self.assertFalse(pool.report_failure("shop", "s1", bound))
        self.assertEqual(pool.acquire("shop", "s1"), bound)
        self.assertTrue(pool.report_failure("shop", "s1", bound))
        picked = pool.acquire("shop", "s1")
        self.assertNotEqual(picked, bound)
        self.assertEqual(pool.switch_log()[-1]["reason"], "exit-quarantined")

    def test_quarantined_exit_recovers_after_cooldown(self):
        clock = FakeClock()
        pool = make_pool(["p1"], sticky_window=30, failure_threshold=1,
                         cooldown=10, time_func=clock)
        from proxysticky import PoolExhaustedError
        self.assertEqual(pool.acquire("shop", "s1"), "p1")
        self.assertTrue(pool.report_failure("shop", "s1", "p1"))
        with self.assertRaises(PoolExhaustedError):
            pool.acquire("shop", "s1")
        clock.advance(11)
        self.assertEqual(pool.acquire("shop", "s1"), "p1")

    def test_sites_are_isolated(self):
        pool = make_pool(["p1", "p2"], sticky_window=30)
        shop_exit = pool.acquire("shop", "s1")
        news_exit = pool.acquire("news", "s1")
        self.assertNotEqual(shop_exit, news_exit)

    def test_quarantine_is_per_site(self):
        pool = make_pool(["p1"], sticky_window=30, failure_threshold=1, cooldown=10)
        from proxysticky import PoolExhaustedError
        pool.acquire("shop", "s1")
        pool.report_failure("shop", "s1", "p1")
        self.assertEqual(pool.acquire("news", "s1"), "p1")
        with self.assertRaises(PoolExhaustedError):
            pool.acquire("shop", "s2")

    def test_error_paths_raise(self):
        from proxysticky import PoolError, StickyPool
        with self.assertRaises(PoolError):
            StickyPool([])
        with self.assertRaises(ValueError):
            StickyPool(["p1"], failure_threshold=0)
        pool = make_pool(["p1"], sticky_window=30)
        with self.assertRaises(PoolError):
            pool.report_failure("shop", "s1", "no-such-proxy")
        with self.assertRaises(ValueError):
            pool.acquire("", "s1")

    def test_status_reports_pool_state(self):
        pool = make_pool(["p1", "p2"], sticky_window=30, failure_threshold=1,
                         cooldown=10)
        bound = pool.acquire("shop", "s1")
        pool.report_failure("shop", "s1", bound)
        status = pool.status()
        self.assertEqual(status["failure_threshold"], 1)
        shop = status["sites"]["shop"]
        self.assertIn(bound, shop["quarantined"])
        self.assertNotIn(bound, shop["available"])
