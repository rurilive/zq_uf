import os
import tempfile
import unittest

from urlqueue import pop, push, stats
from urlqueue import queue


class QueueTest(unittest.TestCase):
    def setUp(self):
        queue._queue.clear()

    def test_fifo(self):
        push("a"); push("b")
        self.assertEqual(pop()["url"], "a")

    def test_priority_saved(self):
        push("a", 1)
        self.assertEqual(pop()["priority"], 1)

    def test_empty_none(self):
        self.assertIsNone(pop())


class AgingQueueTest(unittest.TestCase):
    """老化、保底、插队上限、重启恢复。用假时钟控制等待时间。"""

    def setUp(self):
        queue._queue.clear()
        queue._seq = 0
        queue._pops_since_guarantee = 0
        queue._guaranteed = 0
        queue._restored = 0
        self._old_clock = queue._clock
        self._old_path = queue._state_path
        queue._state_path = None  # 默认不落盘, 恢复测试单独指定
        self.t = 1000.0
        queue._clock = lambda: self.t

    def tearDown(self):
        queue._clock = self._old_clock
        queue._state_path = self._old_path

    def test_aging_promotes_old_link(self):
        push("old", 1)
        self.t += 300.0  # old 先等 300s
        push("new", 5)   # new 刚到, old 已 +5 分 → 6 > 5
        self.assertEqual(pop()["url"], "old")

    def test_aging_not_enough_keeps_priority(self):
        push("old", 1)
        self.t += 60.0   # old 只 +1 分 → 2 < 5
        push("new", 5)
        self.assertEqual(pop()["url"], "new")

    def test_same_score_fifo(self):
        push("x", 3)
        push("y", 3)
        self.assertEqual(pop()["url"], "x")

    def test_guarantee_oldest_within_window(self):
        push("low", 1)
        for i in range(10):
            push("high%d" % i, 9)
        seen = [pop()["url"] for _ in range(queue.GUARANTEE_WINDOW)]
        self.assertIn("low", seen)  # 窗口内保底出队
        self.assertEqual(seen[-1], "low")  # 前 4 次高分插队, 第 5 次保底
        self.assertEqual(stats()["guaranteed"], 1)

    def test_jump_cap(self):
        push("low", 1)
        for i in range(8):
            push("h%d" % i, 9)
        streak = 0
        for _ in range(8):
            if pop()["url"] == "low":
                break
            streak += 1
        # 高优连续插队不超过 GUARANTEE_WINDOW - 1 次
        self.assertLessEqual(streak, queue.GUARANTEE_WINDOW - 1)

    def test_stats_reports_scores_and_promoted(self):
        push("a", 1)
        self.t += 120.0  # a 老化 +2 分, 达到提权口径
        push("b", 9)     # b 刚到, 无老化加分
        s = stats()
        self.assertEqual(s["promoted"], 1)
        self.assertEqual(s["size"], 2)
        self.assertEqual(dict(s["scores"])["a"], 3.0)

    def test_restart_recovery(self):
        path = os.path.join(tempfile.mkdtemp(), "state.json")
        queue._state_path = path
        push("old", 1)
        self.t += 600.0  # old 先等 600s
        push("hot", 9)
        # 模拟重启: 清空内存, 从磁盘恢复
        queue._queue.clear()
        restored = queue.load()
        self.assertEqual(restored, 2)
        self.assertEqual(stats()["restored"], 2)
        # 入队时间保留, 重启后按分数恢复: old → 1 + 10 = 11 > 9
        self.assertEqual(pop()["url"], "old")
        queue._state_path = None


if __name__ == "__main__":
    unittest.main()
