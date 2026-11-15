import time
import unittest

from jobwindow import close, open


class WindowTest(unittest.TestCase):
    """原有 3 个断言保持不变。"""

    def setUp(self):
        from jobwindow import reset

        reset()

    def test_open_ignores_upstream(self):
        result = open({"name": "w1", "upstream": ["job-a"]})
        self.assertTrue(result["opened"])
        self.assertFalse(result["waited"])

    def test_open_has_no_catch_up(self):
        self.assertFalse(open({"name": "w2"})["catch_up"])

    def test_close_aborts_nothing(self):
        self.assertEqual(close({"name": "w1"})["aborted"], [])


class FakeClock:
    """假时钟：sleep 直接把时间往前推，测试不依赖真实等待。"""

    def __init__(self, now=1000.0):
        self.now = now
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ResetMixin:
    def setUp(self):
        from jobwindow import configure, reset

        reset()
        self.clock = FakeClock()
        configure(clock=self.clock.time, sleep=self.clock.sleep)

    def tearDown(self):
        from jobwindow import configure, reset

        reset()
        configure(clock=time.time, sleep=time.sleep)


class DependencyTest(ResetMixin, unittest.TestCase):
    def test_pending_upstream_skips_window(self):
        from jobwindow import mark_job

        mark_job("extract", "pending")
        result = open(
            {
                "name": "hourly",
                "upstream": ["extract"],
                "dependency_policy": "skip",
            }
        )
        self.assertFalse(result["opened"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "upstream_pending")
        self.assertEqual(result["pending_upstream"], ["extract"])

    def test_pending_upstream_waits_then_opens(self):
        from jobwindow import mark_job

        mark_job("extract", "pending")
        # 第一次检查时上游未完成，推进时钟后完成的模拟：用 check 回调
        calls = {"n": 0}

        def check():
            calls["n"] += 1
            return calls["n"] >= 2

        result = open(
            {
                "name": "hourly",
                "upstream": [{"name": "extract", "check": check}],
                "dependency_policy": "wait",
                "wait_timeout": 1.0,
            }
        )
        self.assertTrue(result["opened"])
        self.assertTrue(result["waited"])
        self.assertGreater(len(self.clock.sleeps), 0)

    def test_wait_times_out_and_skips(self):
        from jobwindow import mark_job

        mark_job("extract", "pending")
        result = open(
            {
                "name": "hourly",
                "upstream": ["extract"],
                "dependency_policy": "wait",
                "wait_timeout": 0.2,
            }
        )
        self.assertFalse(result["opened"])
        self.assertEqual(result["reason"], "upstream_timeout")
        self.assertTrue(result["alerts"])
        self.assertGreaterEqual(self.clock.now, 1000.2)

    def test_multiple_upstreams_all_must_finish(self):
        from jobwindow import mark_job

        mark_job("a", "completed")
        mark_job("b", "pending")
        result = open(
            {
                "name": "multi",
                "upstream": ["a", "b"],
                "dependency_policy": "skip",
            }
        )
        self.assertFalse(result["opened"])
        self.assertEqual(result["pending_upstream"], ["b"])

        mark_job("b", "completed")
        reopened = open(
            {
                "name": "multi",
                "upstream": ["a", "b"],
                "dependency_policy": "skip",
            }
        )
        self.assertTrue(reopened["opened"])

    def test_upstream_mapping_form(self):
        result = open(
            {
                "name": "map",
                "upstream": {"a": True, "b": {"status": "pending"}},
                "dependency_policy": "skip",
            }
        )
        self.assertFalse(result["opened"])
        self.assertEqual(result["pending_upstream"], ["b"])

    def test_failed_upstream_blocks_downstream_and_alerts(self):
        from jobwindow import fail_job

        fail_job("extract", "OOM")
        result = open({"name": "hourly", "upstream": ["extract"]})
        self.assertFalse(result["opened"])
        self.assertEqual(result["reason"], "upstream_failed")
        self.assertTrue(result["alerts"])
        self.assertIn("extract", result["alerts"][0])
        self.assertTrue(result["failed_upstream"])


class CatchUpTest(ResetMixin, unittest.TestCase):
    def test_missed_window_catches_up_once(self):
        window = {"name": "daily", "scheduled_at": 900.0, "window_seconds": 10}
        first = open(window)
        self.assertTrue(first["opened"])
        self.assertTrue(first["catch_up"])
        self.assertTrue(first["catch_up_reason"])
        close(window)

        second = open(window)
        self.assertFalse(second["opened"])
        self.assertFalse(second["catch_up"])
        self.assertEqual(second["reason"], "catch_up_exhausted")

    def test_new_occurrence_can_catch_up_again(self):
        first = open({"name": "daily", "scheduled_at": 900.0, "window_seconds": 10})
        self.assertTrue(first["catch_up"])
        close({"name": "daily"})

        later = open({"name": "daily", "scheduled_at": 980.0, "window_seconds": 10})
        self.assertTrue(later["opened"])
        self.assertTrue(later["catch_up"])

    def test_catch_up_policy_never(self):
        result = open(
            {
                "name": "daily",
                "scheduled_at": 900.0,
                "window_seconds": 10,
                "catch_up_policy": "never",
            }
        )
        self.assertFalse(result["opened"])
        self.assertEqual(result["reason"], "missed_window")
        self.assertTrue(result["alerts"])

    def test_in_window_not_marked_catch_up(self):
        result = open({"name": "now", "scheduled_at": 990.0, "window_seconds": 20})
        self.assertTrue(result["opened"])
        self.assertFalse(result["catch_up"])
        self.assertIsNone(result["catch_up_reason"])

    def test_blocked_attempt_does_not_consume_catch_up(self):
        """补跑被上游挡住时并未真正补跑，上游就绪后仍可补一次。"""
        from jobwindow import mark_job

        missed = {"name": "daily", "scheduled_at": 900.0, "window_seconds": 10, "upstream": ["up"]}
        mark_job("up", "pending")
        blocked = open(dict(missed, dependency_policy="skip"))
        self.assertFalse(blocked["opened"])
        self.assertFalse(blocked["catch_up"])

        mark_job("up", "completed")
        retried = open(missed)
        self.assertTrue(retried["opened"])
        self.assertTrue(retried["catch_up"])
        close(missed)

        exhausted = open(missed)
        self.assertEqual(exhausted["reason"], "catch_up_exhausted")


class ConcurrencyTest(ResetMixin, unittest.TestCase):
    def test_concurrency_limit_skips_extra_windows(self):
        open({"name": "a", "resource_group": "db", "max_concurrency": 1})
        second = open({"name": "b", "resource_group": "db", "max_concurrency": 1})
        self.assertFalse(second["opened"])
        self.assertEqual(second["reason"], "concurrency_limit")
        self.assertEqual(second["concurrency"], 1)

        close({"name": "a"})
        third = open({"name": "b", "resource_group": "db", "max_concurrency": 1})
        self.assertTrue(third["opened"])
        self.assertEqual(third["concurrency"], 1)

    def test_resource_groups_are_isolated(self):
        open({"name": "a", "resource_group": "db", "max_concurrency": 1})
        other = open({"name": "b", "resource_group": "api", "max_concurrency": 1})
        self.assertTrue(other["opened"])
        self.assertEqual(other["group"], "api")

    def test_concurrency_wait_policy(self):
        open({"name": "a", "resource_group": "db", "max_concurrency": 1})
        result = open(
            {
                "name": "b",
                "resource_group": "db",
                "max_concurrency": 1,
                "on_concurrency_limit": "wait",
                "wait_timeout": 0.2,
            }
        )
        self.assertFalse(result["opened"])
        self.assertEqual(result["reason"], "concurrency_timeout")
        self.assertTrue(result["waited"])

    def test_group_limit_applies_to_all_windows(self):
        from jobwindow import set_group_limit

        set_group_limit("db", 1)
        open({"name": "a", "resource_group": "db"})
        second = open({"name": "b", "resource_group": "db"})
        self.assertFalse(second["opened"])
        self.assertEqual(second["reason"], "concurrency_limit")

    def test_skipped_windows_do_not_occupy_slots(self):
        from jobwindow import mark_job

        mark_job("up", "pending")
        open(
            {
                "name": "a",
                "resource_group": "db",
                "upstream": ["up"],
                "dependency_policy": "skip",
            }
        )
        opened = open(
            {"name": "b", "resource_group": "db", "max_concurrency": 1}
        )
        self.assertTrue(opened["opened"])


class CloseAndStatusTest(ResetMixin, unittest.TestCase):
    def test_close_cleans_unfinished_tasks(self):
        from jobwindow import complete_task

        open(
            {
                "name": "w",
                "tasks": ["extract", "load"],
            }
        )
        complete_task({"name": "w"}, "extract")
        result = close({"name": "w"})
        self.assertEqual(result["aborted"], ["load"])
        self.assertEqual(result["cleaned_tasks"], ["load"])

    def test_close_releases_concurrency(self):
        open({"name": "a", "resource_group": "db", "max_concurrency": 1})
        close({"name": "a"})
        self.assertTrue(
            open({"name": "b", "resource_group": "db", "max_concurrency": 1})[
                "opened"
            ]
        )

    def test_status_reports_open_and_closed(self):
        from jobwindow import status

        open({"name": "w", "resource_group": "db", "tasks": ["t"]})
        state = status({"name": "w"})
        self.assertEqual(state["status"], "open")
        self.assertTrue(state["opened"])
        self.assertEqual(state["concurrency"], 1)
        self.assertFalse(state["tasks"]["t"]["done"])

        close({"name": "w"})
        state = status({"name": "w"})
        self.assertEqual(state["status"], "closed")
        self.assertFalse(state["opened"])
        self.assertEqual(state["concurrency"], 0)
        self.assertEqual(state["aborted"], ["t"])

    def test_status_unknown_window(self):
        from jobwindow import status

        self.assertEqual(status({"name": "ghost"})["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
