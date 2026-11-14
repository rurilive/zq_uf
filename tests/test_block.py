import unittest

from blockvote import allow, observe
from blockvote import block


class BlockTest(unittest.TestCase):
    def setUp(self):
        block._blocked.clear()

    def test_403_blocks(self):
        self.assertTrue(observe("shop", "403"))

    def test_captcha_ignored(self):
        self.assertFalse(observe("shop", "captcha"))

    def test_no_recovery(self):
        observe("shop", "403")
        observe("shop", "200")
        self.assertFalse(allow("shop"))


class MultiSignalTest(unittest.TestCase):
    def setUp(self):
        block._blocked.clear()
        self.clock = [0.0]
        block._now = lambda: self.clock[0]

    def tearDown(self):
        block._now = None
        block._blocked.clear()

    def advance(self, seconds):
        self.clock[0] += seconds

    def test_weak_signals_vote_before_break(self):
        # 单个弱信号不熔断，多个信号加权累计过阈才熔断。
        self.assertFalse(observe("shop", "captcha"))
        self.assertEqual(block.votes("shop")[0], 2)
        self.assertFalse(observe("shop", "redirect"))  # 累计 3，未严格过阈
        self.assertEqual(block.votes("shop")[0], 3)
        self.assertTrue(observe("shop", "ratelimit"))  # 累计 5，过阈熔断
        self.assertEqual(block.votes("shop")[0], 5)
        self.assertEqual(block.status("shop"), block.OPEN)

    def test_all_five_signals_weighted(self):
        for signal, weight in block.SIGNAL_WEIGHTS.items():
            blocked = observe("s-" + signal, signal)
            if weight > block.BLOCK_THRESHOLD:
                self.assertTrue(blocked, signal)
            else:
                self.assertFalse(blocked, signal)

    def test_threshold_strictly_greater(self):
        # 403 为强信号，单次即过阈熔断（与旧断言兼容）。
        self.assertTrue(observe("shop", "403"))
        # 两个 captcha 累计 4 票才熔断，单个 captcha 不熔断。
        block._blocked.clear()
        self.assertFalse(observe("news", "captcha"))
        self.assertTrue(observe("news", "captcha"))

    def test_success_resets_votes_when_closed(self):
        observe("shop", "captcha")
        observe("shop", "200")
        total, _ = block.votes("shop")
        self.assertEqual(total, 0)
        self.assertTrue(allow("shop"))

    def test_vote_window_expires(self):
        observe("shop", "captcha")
        self.advance(block.VOTE_WINDOW_SECONDS + 1)
        total, _ = block.votes("shop")
        self.assertEqual(total, 0)


class ProbeRecoveryTest(unittest.TestCase):
    def setUp(self):
        block._blocked.clear()
        self.clock = [0.0]
        block._now = lambda: self.clock[0]

    def tearDown(self):
        block._now = None
        block._blocked.clear()

    def advance(self, seconds):
        self.clock[0] += seconds

    def test_probe_recovery_after_cooldown(self):
        observe("shop", "403")
        self.assertEqual(block.status("shop"), block.OPEN)

        # 冷却期内的成功不触发探针。
        observe("shop", "200")
        self.assertEqual(block.status("shop"), block.OPEN)

        self.advance(block.COOLDOWN_SECONDS)
        # 首个成功信号让站点进入观察态并完成第一次探针。
        observe("shop", "200")
        self.assertEqual(block.status("shop"), block.HALF_OPEN)
        self.assertEqual(block.stats("shop")["probe_successes"], 1)

        # 探针间隔内的成功被跳过。
        observe("shop", "200")
        self.assertEqual(block.stats("shop")["probe_successes"], 1)

        # 低频探针连续成功达到要求后恢复。
        for _ in range(block.RECOVERY_SUCCESSES - 1):
            self.advance(block.PROBE_INTERVAL_SECONDS)
            observe("shop", "200")
        self.assertEqual(block.status("shop"), block.CLOSED)
        self.assertTrue(allow("shop"))

    def test_ban_signal_in_half_open_reopens(self):
        observe("shop", "403")
        self.advance(block.COOLDOWN_SECONDS)
        observe("shop", "200")
        self.assertEqual(block.status("shop"), block.HALF_OPEN)
        observe("shop", "captcha")
        self.assertEqual(block.status("shop"), block.OPEN)
        self.assertFalse(allow("shop"))
        # 重新冷却，立即成功不会再进观察态。
        observe("shop", "200")
        self.assertEqual(block.status("shop"), block.OPEN)


class WhitelistTest(unittest.TestCase):
    def setUp(self):
        block._blocked.clear()
        self.clock = [0.0]
        block._now = lambda: self.clock[0]

    def tearDown(self):
        block._now = None
        block._blocked.clear()

    def advance(self, seconds):
        self.clock[0] += seconds

    def test_whitelist_overrides_break_and_expires(self):
        observe("shop", "403")
        self.assertFalse(allow("shop"))

        block.add_whitelist("shop", 60)
        self.assertTrue(allow("shop"))
        # 白名单期内的信号不参与投票。
        self.assertFalse(observe("shop", "403"))
        self.assertEqual(block.status("shop"), block.CLOSED)

        self.advance(61)
        # 白名单是覆盖层，过期后恢复底层熔断判定。
        self.assertFalse(allow("shop"))
        self.assertEqual(block.status("shop"), block.OPEN)
        kinds = [event["kind"] for event in block.events("shop")]
        self.assertIn("whitelist_expired", kinds)

    def test_invalid_ttl_rejected(self):
        with self.assertRaises(ValueError):
            block.add_whitelist("shop", 0)
