import unittest

from crawlwarm import current, dump_state, load_state, reset, warmup
from crawlwarm.warm import COOLDOWN_SECONDS, STAGES


class WarmTest(unittest.TestCase):
    def test_warmup_returns_concurrency(self):
        self.assertEqual(warmup("shop")["concurrency"], 8)

    def test_current_default(self):
        self.assertEqual(current("news"), 8)

    def test_current_after_warmup(self):
        warmup("shop")
        self.assertEqual(current("shop"), 8)


class LadderTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_step_promotion(self):
        # 每个高成功率窗口升一档：1 -> 2 -> 4 -> 8，到顶后不再升
        expected = [2, 4, 8, 8]
        for want in expected:
            report = warmup("shop", window=(10, 0), now=0.0)
            self.assertEqual(report["concurrency"], want)
        self.assertEqual(report["stage"], len(STAGES) - 1)
        self.assertEqual(current("shop"), 8)

    def test_small_window_not_evaluated(self):
        # 观察窗口样本不足 MIN_WINDOW，不升档
        report = warmup("shop", window=(2, 0), now=0.0)
        self.assertEqual(report["concurrency"], 1)

    def test_failure_regress_and_snapshot(self):
        warmup("shop", window=(10, 0), now=0.0)   # -> 2
        warmup("shop", window=(10, 0), now=1.0)   # -> 4
        report = warmup("shop", window=(1, 9), now=2.0)  # 成功率 0.1 -> 回退
        self.assertEqual(report["concurrency"], 2)
        self.assertEqual(len(report["backoffs"]), 1)
        backoff = report["backoffs"][0]
        # 回退记录保留上一档快照
        self.assertEqual(backoff["from"]["concurrency"], 4)
        self.assertEqual(backoff["from"]["stage"], 2)
        self.assertEqual(backoff["to_concurrency"], 2)
        self.assertEqual(current("shop"), 2)

    def test_consecutive_failures_regress(self):
        warmup("shop", window=(10, 0), now=0.0)   # -> 2
        # 窗口整体成功率 0.6 但末尾连续失败达到上限，也回退
        report = warmup("shop", window=[True] * 6 + [False] * 4, now=1.0)
        self.assertEqual(report["concurrency"], 1)

    def test_cooldown_blocks_promotion(self):
        warmup("shop", window=(10, 0), now=0.0)   # -> 2
        warmup("shop", window=(0, 5), now=1.0)    # 回退 -> 1，进入冷却
        # 冷却内即使全成功也不升档
        report = warmup("shop", window=(10, 0), now=1.0 + COOLDOWN_SECONDS / 2)
        self.assertEqual(report["concurrency"], 1)
        self.assertTrue(report["in_cooldown"])
        # 冷却结束后正常升档
        report = warmup("shop", window=(10, 0), now=1.0 + COOLDOWN_SECONDS + 1)
        self.assertEqual(report["concurrency"], 2)
        self.assertFalse(report["in_cooldown"])

    def test_restart_recovery(self):
        warmup("shop", window=(10, 0), now=0.0)   # -> 2
        warmup("shop", window=(10, 0), now=1.0)   # -> 4
        warmup("shop", window=(0, 5), now=2.0)    # 回退 -> 2
        snapshot = dump_state()

        reset()  # 模拟重启，状态丢失
        self.assertEqual(current("shop"), 8)      # 未恢复前按默认

        load_state(snapshot)  # 重启后从当前档继续
        self.assertEqual(current("shop"), 2)
        report = warmup("shop", window=(10, 0), now=2.0 + COOLDOWN_SECONDS + 1)
        self.assertEqual(report["concurrency"], 4)
        # 回退记录也随状态恢复
        self.assertEqual(len(report["backoffs"]), 1)


if __name__ == "__main__":
    unittest.main()
