import unittest

from retrywindow import RetryWindowError, abandoned, cancel, due, schedule, windows
from retrywindow import retry


class RetryTest(unittest.TestCase):
    def setUp(self):
        retry._tasks.clear()

    def test_first_attempt_retries(self):
        self.assertTrue(schedule("t1", "shop")["retry"])

    def test_three_attempts_stop(self):
        for _ in range(3):
            result = schedule("t1", "shop")
        self.assertFalse(result["retry"])

    def test_due_lists_pending(self):
        schedule("t1", "shop")
        self.assertIn("t1", due())


class SharedWindowTest(unittest.TestCase):
    """站点共享窗口、抖动、放弃标记和取消。"""

    def setUp(self):
        retry._tasks.clear()
        retry._sites.clear()
        retry._abandoned.clear()
        retry._cancelled.clear()
        retry._rng.seed(0)
        self._real_clock = retry._clock
        self.now = [1000.0]
        retry._clock = lambda: self.now[0]

    def tearDown(self):
        retry._clock = self._real_clock

    def test_same_site_shares_window(self):
        schedule("t1", "shop")
        schedule("t2", "shop")
        win = windows()["shop"]
        self.assertEqual(win["used"], 2)
        self.assertEqual(win["window_start"], 1000.0)
        self.assertEqual(win["window_end"], 1000.0 + retry.WINDOW_SECONDS)

    def test_different_sites_have_independent_windows(self):
        schedule("t1", "shop")
        schedule("t2", "news")
        self.assertEqual(windows()["shop"]["used"], 1)
        self.assertEqual(windows()["news"]["used"], 1)

    def test_budget_exhausted_defers_to_next_window(self):
        budget = retry.SITE_BUDGET
        for i in range(budget):
            schedule(f"t{i}", "shop")
        result = schedule("overflow", "shop")
        self.assertGreaterEqual(result["due_at"], 1000.0 + retry.WINDOW_SECONDS)
        self.assertEqual(windows()["shop"]["remaining"], 0)

    def test_window_slides_after_expiry(self):
        schedule("t1", "shop")
        self.now[0] += retry.WINDOW_SECONDS + 1.0
        schedule("t2", "shop")
        win = windows()["shop"]
        self.assertEqual(win["used"], 1)  # 旧窗口的占用不带入新窗口

    def test_jitter_spreads_due_times_within_window(self):
        due_ats = [schedule(f"t{i}", "shop")["due_at"] for i in range(5)]
        self.assertGreater(len(set(due_ats)), 1)  # 不同时重试
        for due_at in due_ats:
            self.assertGreaterEqual(due_at, 1000.0 + retry.BASE_DELAY)
            self.assertLessEqual(
                due_at, 1000.0 + retry.BASE_DELAY + retry.JITTER_MAX)

    def test_abandon_after_max_duration(self):
        schedule("t1", "shop")
        self.now[0] += retry.MAX_DURATION + 1.0
        result = schedule("t1", "shop")
        self.assertFalse(result["retry"])
        self.assertTrue(result["abandoned"])
        self.assertIn("总时长", result["reason"])
        self.assertEqual(abandoned()[0]["task"], "t1")
        self.assertNotIn("t1", due())

    def test_abandon_after_max_attempts(self):
        for _ in range(3):
            result = schedule("t1", "shop")
        self.assertFalse(result["retry"])
        self.assertIn("最大尝试次数", abandoned()[0]["reason"])

    def test_abandoned_task_cannot_reschedule(self):
        for _ in range(3):
            schedule("t1", "shop")
        with self.assertRaises(RetryWindowError):
            schedule("t1", "shop")

    def test_cancel_stops_triggering(self):
        schedule("t1", "shop")
        cancel("t1")
        self.assertNotIn("t1", due())
        self.assertNotIn("t1", due(now=self.now[0] + 9999.0))
        with self.assertRaises(RetryWindowError):
            schedule("t1", "shop")  # 取消后不再排期

    def test_cancel_unknown_task_raises(self):
        with self.assertRaises(RetryWindowError):
            cancel("ghost")

    def test_due_filters_by_now(self):
        schedule("t1", "shop")
        self.assertEqual(due(now=1000.0), [])
        self.assertEqual(due(now=1000.0 + 100.0), ["t1"])

    def test_invalid_arguments_raise(self):
        with self.assertRaises(ValueError):
            schedule("", "shop")
        with self.assertRaises(ValueError):
            schedule("t1", "")
        with self.assertRaises(ValueError):
            schedule("t1", None)

    def test_task_cannot_move_site(self):
        schedule("t1", "shop")
        with self.assertRaises(RetryWindowError):
            schedule("t1", "news")
