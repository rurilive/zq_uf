import unittest

import crawlcap.cap as cap
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


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    """每个用例独立时钟、独立站点状态。"""

    def setUp(self):
        self.clock = _Clock()
        self._real_now = cap._now
        cap._now = self.clock
        cap.reset()

    def tearDown(self):
        cap._now = self._real_now
        cap.reset()


class LadderTest(Base):
    def test_ramp_ladder(self):
        for _ in range(4):
            r = report("shop", True, 0.1)
        self.assertEqual(r["event"], "ramp")
        self.assertEqual(r["limit"], 8)
        for _ in range(4):
            r = report("shop", True, 0.1)
        self.assertEqual(r["limit"], 16)

    def test_ramp_capped_at_max(self):
        for _ in range(20):
            r = report("shop", True, 0.1)
        self.assertEqual(r["limit"], 16)

    def test_failure_resets_success_streak(self):
        report("shop", True, 0.1)
        report("shop", True, 0.1)
        report("shop", False, 0.1)
        for _ in range(3):
            r = report("shop", True, 0.1)
        self.assertEqual(r["limit"], 4)
        r = report("shop", True, 0.1)
        self.assertEqual(r["limit"], 8)


class BackoffTest(Base):
    def test_backoff_then_cooldown(self):
        for _ in range(3):
            r = report("shop", False, 0.5)
        self.assertEqual(r["event"], "backoff")
        self.assertEqual(r["phase"], "cooldown")
        self.assertEqual(r["limit"], 2)
        self.assertEqual(r["backoff"]["from"], 4)
        self.assertEqual(r["backoff"]["to"], 2)

        g = acquire("shop")
        self.assertFalse(g["ok"])
        self.assertEqual(g["reason"], "cooldown")
        self.assertGreater(g["wait"], 0)

        self.clock.t += 5.0
        g = acquire("shop")
        self.assertTrue(g["ok"])
        self.assertEqual(g["phase"], "ramp")

    def test_backoff_floor(self):
        for _ in range(9):
            r = report("shop", False, 0.5)
        self.assertEqual(r["limit"], 1)
        self.assertEqual(len([b for b in [r["backoff"]] if b]), 1)


class QuotaTest(Base):
    def setUp(self):
        super().setUp()
        self._cap, self._rate = cap._QUOTA_CAPACITY, cap._QUOTA_RATE
        cap._QUOTA_CAPACITY = 2
        cap._QUOTA_RATE = 0.5

    def tearDown(self):
        cap._QUOTA_CAPACITY, cap._QUOTA_RATE = self._cap, self._rate
        super().tearDown()

    def test_acquire_consumes_quota(self):
        self.assertEqual(acquire("shop")["quota"], 1)
        self.assertEqual(acquire("shop")["quota"], 0)

    def test_quota_exhausted_returns_wait_not_raise(self):
        acquire("shop")
        acquire("shop")
        g = acquire("shop")
        self.assertFalse(g["ok"])
        self.assertEqual(g["reason"], "quota")
        self.assertAlmostEqual(g["wait"], 2.0)  # 缺 1 令牌，速率 0.5/s
        self.clock.t += 2.0
        self.assertTrue(acquire("shop")["ok"])

    def test_over_quota_queues(self):
        acquire("shop")
        acquire("shop")
        self.assertEqual(acquire("shop")["queue"], 1)
        g = acquire("shop")
        self.assertEqual(g["queue"], 2)
        self.assertGreater(g["wait"], 0)


class FullAndReclaimTest(Base):
    def test_full_returns_lease_wait(self):
        for _ in range(4):
            acquire("shop")
        g = acquire("shop")
        self.assertFalse(g["ok"])
        self.assertEqual(g["reason"], "full")
        self.assertAlmostEqual(g["wait"], cap._LEASE_TTL)

    def test_slow_holders_are_reclaimed(self):
        for _ in range(4):
            acquire("shop")
        self.assertFalse(acquire("shop")["ok"])
        self.clock.t += cap._LEASE_TTL + 0.1
        with self.assertLogs("crawlcap", level="INFO") as cm:
            g = acquire("shop")
        self.assertTrue(g["ok"])
        self.assertTrue(any("reclaim" in line for line in cm.output))


class SharedStateTest(Base):
    def test_phase_shared_across_tasks(self):
        # 任务 A 连续失败，把站点打入冷却
        for _ in range(3):
            report("shop", False, 0.5)
        # 任务 B 申请同一站点，看到同一个冷却状态
        g = acquire("shop")
        self.assertFalse(g["ok"])
        self.assertEqual(g["reason"], "cooldown")
        # 任务 A 的爬升进度，任务 B 也能看到
        cap.reset("shop")
        for _ in range(4):
            report("shop", True, 0.1)
        self.assertEqual(acquire("shop")["limit"], 8)


class LogTraceTest(Base):
    def test_ramp_backoff_queue_logged(self):
        cap._QUOTA_CAPACITY, old_cap = 1, cap._QUOTA_CAPACITY
        try:
            with self.assertLogs("crawlcap", level="INFO") as cm:
                for _ in range(4):
                    report("shop", True, 0.1)
                for _ in range(3):
                    report("shop", False, 0.5)
                acquire("news")
                acquire("news")  # 配额不足，排队
            out = "\n".join(cm.output)
            self.assertIn("ramp", out)
            self.assertIn("backoff", out)
            self.assertIn("queue", out)
        finally:
            cap._QUOTA_CAPACITY = old_cap


if __name__ == "__main__":
    unittest.main()
