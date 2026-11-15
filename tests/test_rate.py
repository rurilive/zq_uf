import unittest

import rateadapt.rate as rate_mod
from rateadapt import acquire, feedback, join_group, records, replay, rollback


class RateTest(unittest.TestCase):
    def test_rate_is_fixed(self):
        self.assertEqual(acquire("a")["rate"], 1.0)

    def test_acquire_always_ok(self):
        self.assertTrue(acquire("b")["ok"])

    def test_feedback_does_not_adjust(self):
        self.assertFalse(feedback("a", 429, 2.0)["adjusted"])


class AdaptiveTest(unittest.TestCase):
    """自适应：慢涨快降、429 降速、预算共用、冷却、回滚与回放。"""

    def setUp(self):
        self.clock = [1000.0]
        self._orig_now = rate_mod._now
        rate_mod._now = lambda: self.clock[0]

    def tearDown(self):
        rate_mod._now = self._orig_now

    def site(self, name):
        # 每个用例独立站点名，避免模块级状态串扰
        return f"{self.id()}:{name}"

    def test_slow_up_then_fast_down(self):
        s = self.site("s")
        for _ in range(9):
            self.assertFalse(feedback(s, 200, 0.1)["adjusted"])
        r = feedback(s, 200, 0.1)  # 连续 10 次成功，上调一档
        self.assertTrue(r["adjusted"])
        self.assertEqual(r["reason"], "success_streak")
        self.assertEqual(r["rate"], 2.0)

        self.clock[0] += rate_mod.COOLDOWN + 1
        feedback(s, 429, 0.5)
        self.assertFalse(feedback(s, 429, 0.5)["adjusted"])  # 占比未到
        r = feedback(s, 429, 0.5)  # 窗口内 429 占比 3/10，一次下两档
        self.assertTrue(r["adjusted"])
        self.assertEqual(r["reason"], "throttled_429")
        self.assertEqual(r["rate"], 0.5)

    def test_429_decrease(self):
        s = self.site("s")
        for _ in range(3):
            feedback(s, 200, 0.1)
        self.assertFalse(feedback(s, 429, 0.5)["adjusted"])  # 样本不足 5
        r = feedback(s, 429, 0.5)  # 2/5 是 429，触发降速
        self.assertTrue(r["adjusted"])
        self.assertEqual(r["reason"], "throttled_429")
        self.assertLess(r["rate"], 1.0)
        self.assertIn("budget", r)

    def test_shared_budget(self):
        a, b, grp = self.site("a"), self.site("b"), self.site("grp")
        join_group(a, grp)
        join_group(b, grp)
        for _ in range(4):  # 组预算 = 1.0 QPS × 4s = 4 份
            self.assertTrue(acquire(a)["ok"])
        r = acquire(b)  # b 自己没发过请求，但组预算已被 a 耗尽
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "budget_exhausted")
        self.assertEqual(r["group"], grp)
        self.clock[0] += rate_mod.BUDGET_WINDOW  # 等待补充后恢复
        self.assertTrue(acquire(b)["ok"])

    def test_cooldown(self):
        s = self.site("s")
        for _ in range(10):
            feedback(s, 200, 0.1)  # 上调到 2.0
        r = None
        for _ in range(10):
            r = feedback(s, 200, 0.1)  # 冷却期内不再调整
        self.assertFalse(r["adjusted"])
        self.assertEqual(r["reason"], "cooldown")
        self.clock[0] += rate_mod.COOLDOWN + 1
        r = feedback(s, 200, 0.1)  # 冷却结束，继续慢涨
        self.assertTrue(r["adjusted"])
        self.assertEqual(r["rate"], 4.0)

    def test_rollback_and_replay(self):
        s = self.site("s")
        for _ in range(10):
            feedback(s, 200, 0.1)  # 1.0 -> 2.0
        r = rollback(s)
        self.assertTrue(r["adjusted"])
        self.assertEqual(r["rate"], 1.0)
        hist = replay(s)
        self.assertEqual([h["reason"] for h in hist],
                         ["success_streak", "rollback"])
        self.assertEqual((hist[0]["from"], hist[0]["to"]), (1.0, 2.0))
        self.assertFalse(rollback(s)["adjusted"])  # 没有更早的档可回

    def test_records_carry_status_and_rtt(self):
        s = self.site("s")
        feedback(s, 429, 2.5)
        rec = records(s)[0]
        self.assertEqual(rec["status"], 429)
        self.assertEqual(rec["rtt"], 2.5)
