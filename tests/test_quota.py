import json
import os
import subprocess
import sys
import tempfile
import unittest

from retryquota import (
    QuotaCoordinator,
    release,
    request,
    reset,
)


class QuotaTest(unittest.TestCase):
    def setUp(self):
        # 每个用例从干净的模块级协调器开始，不受环境变量/用例顺序影响。
        reset(global_limit=64, site_limit=16, lease_seconds=300, state_path=None)

    def test_request_always_granted(self):
        for _ in range(10):
            self.assertTrue(request("task-a", "shop")["granted"])

    def test_round_counts_per_task(self):
        request("task-b", "shop")
        self.assertEqual(request("task-b", "shop")["round"], 2)

    def test_release_reduces_round(self):
        request("task-c", "shop")
        self.assertEqual(release("task-c")["round"], 0)


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TwoLevelQuotaTest(unittest.TestCase):
    def make(self, **kwargs):
        options = {
            "global_limit": 3,
            "site_limit": 2,
            "lease_seconds": None,
        }
        options.update(kwargs)
        return QuotaCoordinator(**options)

    def test_site_limit_caps_single_site(self):
        quota = self.make()
        first = quota.request("t1", "shop")
        second = quota.request("t2", "shop")
        third = quota.request("t3", "shop")

        self.assertTrue(first["granted"])
        self.assertTrue(second["granted"])
        self.assertFalse(third["granted"])
        self.assertTrue(third["queued"])
        self.assertEqual(third["blocked_by"], "site")
        self.assertEqual(third["queue_position"], 1)
        self.assertEqual(quota.snapshot()["sites"]["shop"]["active"], 2)

    def test_global_limit_caps_across_sites(self):
        quota = self.make(global_limit=2, site_limit=5)
        self.assertTrue(quota.request("t1", "shop")["granted"])
        self.assertTrue(quota.request("t2", "api")["granted"])
        blocked = quota.request("t3", "news")
        self.assertFalse(blocked["granted"])
        self.assertEqual(blocked["blocked_by"], "global")

        quota.release("t1")
        self.assertEqual(quota.snapshot()["active_total"], 2)

    def test_both_limits_apply_at_once(self):
        quota = self.make(global_limit=1, site_limit=1)
        self.assertTrue(quota.request("t1", "shop")["granted"])
        blocked = quota.request("t2", "shop")
        self.assertFalse(blocked["granted"])
        self.assertEqual(blocked["blocked_by"], "site+global")

        # 释放后只补发一个，仍然受两级上限共同约束。
        granted = quota.release("t1")["granted_from_queue"]
        self.assertEqual([item["task"] for item in granted], ["t2"])
        state = quota.snapshot()
        self.assertEqual(state["active_total"], 1)
        self.assertEqual(state["queued_total"], 0)

    def test_per_site_override(self):
        quota = self.make(global_limit=10, site_limit=3, site_limits={"api": 1})
        self.assertTrue(quota.request("t1", "api")["granted"])
        self.assertEqual(quota.request("t2", "api")["blocked_by"], "site")
        # 其它站点仍用默认站点上限。
        self.assertTrue(quota.request("t3", "shop")["granted"])
        self.assertTrue(quota.request("t4", "shop")["granted"])
        self.assertTrue(quota.request("t5", "shop")["granted"])
        self.assertFalse(quota.request("t6", "shop")["granted"])


class QueueTest(unittest.TestCase):
    def make(self, **kwargs):
        options = {
            "global_limit": 1,
            "site_limit": 1,
            "lease_seconds": None,
        }
        options.update(kwargs)
        return QuotaCoordinator(**options)

    def test_excess_requests_queue_in_order(self):
        quota = self.make()
        quota.request("t0", "shop")
        first = quota.request("t1", "shop")
        second = quota.request("t2", "shop")

        self.assertEqual(first["queue_position"], 1)
        self.assertEqual(second["queue_position"], 2)
        self.assertEqual(quota.snapshot()["queued_total"], 2)

        released = quota.release("t0")
        self.assertTrue(released["released"])
        self.assertEqual(released["granted_from_queue"][0]["task"], "t1")

        state = quota.snapshot()
        self.assertEqual([entry["task"] for entry in state["queue"]], ["t2"])
        self.assertEqual([item["task"] for item in state["active"]], ["t1"])

    def test_priority_decides_dequeue(self):
        quota = self.make()
        quota.request("holder", "shop")
        quota.request("low", "shop")
        quota.request("high", "shop")
        quota.request("mid", "shop")
        quota.set_priority("high", 9)
        quota.set_priority("mid", 5)
        quota.set_priority("low", 0)

        self.assertEqual(
            [entry["task"] for entry in quota.snapshot()["queue"]],
            ["high", "mid", "low"],
        )

        self.assertEqual(quota.release("holder")["granted_from_queue"][0]["task"], "high")
        self.assertEqual(quota.release("high")["granted_from_queue"][0]["task"], "mid")
        self.assertEqual(quota.release("mid")["granted_from_queue"][0]["task"], "low")

    def test_priority_can_be_set_before_queueing(self):
        quota = self.make(priorities={"urgent": 7}, global_limit=1, site_limit=1)
        quota.request("holder", "shop")
        quota.request("plain", "shop")
        queued = quota.request("urgent", "shop")

        self.assertEqual(queued["priority"], 7)
        self.assertEqual(quota.release("holder")["granted_from_queue"][0]["task"], "urgent")

    def test_new_high_priority_request_jumps_queue(self):
        quota = self.make(global_limit=2, site_limit=2, priorities={"urgent": 100})
        quota.request("holder-1", "shop")
        quota.request("holder-2", "shop")
        quota.request("plain", "shop")
        self.assertFalse(quota.request("urgent", "shop")["granted"])

        granted = quota.release("holder-1")["granted_from_queue"]
        self.assertEqual([item["task"] for item in granted], ["urgent"])

    def test_full_site_does_not_block_other_sites(self):
        quota = self.make(global_limit=3, site_limit=2)
        quota.request("shop-1", "shop")
        quota.request("shop-2", "shop")
        queued = quota.request("shop-3", "shop")  # shop 满，只有它排队
        self.assertTrue(queued["queued"])
        # shop 满并不影响别的站点继续拿全局名额。
        self.assertTrue(quota.request("api-1", "api")["granted"])
        self.assertEqual(quota.snapshot()["queued_total"], 1)

    def test_head_of_line_skipped_when_its_site_is_full(self):
        quota = self.make(global_limit=2, site_limit=1)
        quota.request("shop-holder", "shop")
        quota.request("api-holder", "api")
        quota.request("shop-waiter", "shop")  # 队首，shop 满
        quota.request("api-waiter", "api")    # 次席，api 满

        # 释放 api 名额时，队首 shop-waiter 仍被跳过，api-waiter 应补位。
        granted = quota.release("api-holder")["granted_from_queue"]
        self.assertEqual([item["task"] for item in granted], ["api-waiter"])
        self.assertEqual(
            [entry["task"] for entry in quota.snapshot()["queue"]], ["shop-waiter"]
        )

    def test_repeated_request_from_same_task_keeps_one_queue_slot(self):
        quota = self.make(global_limit=1, site_limit=1)
        quota.request("holder", "shop")
        first = quota.request("waiter", "shop")
        again = quota.request("waiter", "shop")

        self.assertEqual(first["position"], 1)
        self.assertEqual(again["position"], 1)
        self.assertEqual(again["reason"], "already_queued")
        self.assertEqual(quota.snapshot()["queued_total"], 1)

        # 一次释放只补发一个许可，不会被重复排队放大。
        granted = quota.release("holder")["granted_from_queue"]
        self.assertEqual(len(granted), 1)
        self.assertEqual(granted[0]["task"], "waiter")

    def test_rechecking_after_queue_grant_returns_the_permit(self):
        quota = self.make(global_limit=1, site_limit=1)
        quota.request("holder", "shop")
        self.assertTrue(quota.request("waiter", "shop")["queued"])

        # 别的任务释放名额后 waiter 已被补发；它自己再查一次应拿到许可，
        # 而不是把自己又排回队列或重复占两个名额。
        quota.release("holder")
        recheck = quota.request("waiter", "shop")
        self.assertTrue(recheck["granted"])
        self.assertEqual(recheck["queue_position"], None)
        self.assertEqual(quota.snapshot()["active_total"], 1)

    def test_polling_after_expiry_gets_the_permit(self):
        clock = FakeClock()
        quota = QuotaCoordinator(
            global_limit=1,
            site_limit=1,
            lease_seconds=5,
            clock=clock,
        )
        quota.request("holder", "shop")
        self.assertTrue(quota.request("waiter", "shop")["queued"])

        clock.advance(6)
        polled = quota.request("waiter", "shop")
        self.assertTrue(polled["granted"])
        self.assertGreaterEqual(polled["expired_permits"], 1)
        self.assertEqual([item["task"] for item in quota.snapshot()["active"]], ["waiter"])


class IdempotentReleaseTest(unittest.TestCase):
    def make(self, **kwargs):
        options = {
            "global_limit": 2,
            "site_limit": 1,
            "lease_seconds": None,
        }
        options.update(kwargs)
        return QuotaCoordinator(**options)

    def test_duplicate_release_is_idempotent(self):
        quota = self.make()
        quota.request("t1", "shop")
        first = quota.release("t1")
        second = quota.release("t1")
        third = quota.release("t1")

        self.assertTrue(first["released"])
        self.assertFalse(second["released"])
        self.assertTrue(second["duplicate"])
        self.assertFalse(third["released"])
        self.assertEqual(quota.snapshot()["active_total"], 0)

    def test_duplicate_release_does_not_freeze_queue(self):
        quota = self.make()
        quota.request("t1", "shop")
        quota.request("t2", "shop")

        quota.release("t1")
        # 重复释放不能挤掉已经补发的许可，也不能释放别人。
        duplicate = quota.release("t1")
        self.assertFalse(duplicate["released"])
        self.assertEqual([item["task"] for item in quota.snapshot()["active"]], ["t2"])

    def test_release_unknown_task_is_idempotent(self):
        quota = self.make()
        result = quota.release("never-seen")
        self.assertFalse(result["released"])
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["queue_length"], 0)

    def test_release_one_permit_at_a_time(self):
        quota = self.make(global_limit=2, site_limit=2)
        quota.request("t1", "shop")
        quota.request("t1", "shop")
        self.assertEqual(quota.snapshot()["task_rounds"], {"t1": 2})

        first = quota.release("t1")
        self.assertTrue(first["released"])
        self.assertEqual(first["round"], 1)
        second = quota.release("t1")
        self.assertTrue(second["released"])
        self.assertEqual(second["round"], 0)
        self.assertTrue(quota.release("t1")["duplicate"])


class LeaseExpiryTest(unittest.TestCase):
    def test_expired_permit_returns_automatically(self):
        clock = FakeClock()
        quota = QuotaCoordinator(
            global_limit=1,
            site_limit=1,
            lease_seconds=10,
            clock=clock,
        )
        granted = quota.request("t1", "shop")
        self.assertEqual(granted["expires_at"], clock.now + 10)

        queued = quota.request("t2", "shop")
        self.assertTrue(queued["queued"])

        clock.advance(11)
        # 超时许可在下一次协调动作被归还，排队任务随即补位。
        state = quota.snapshot()
        self.assertGreaterEqual(state["expired_count_just_now"], 1)
        self.assertEqual([item["task"] for item in state["active"]], ["t2"])
        self.assertEqual(state["queued_total"], 0)

    def test_expired_release_is_idempotent(self):
        clock = FakeClock()
        quota = QuotaCoordinator(
            global_limit=1,
            site_limit=1,
            lease_seconds=5,
            clock=clock,
        )
        quota.request("t1", "shop")
        clock.advance(6)

        released = quota.release("t1")
        self.assertFalse(released["released"])
        self.assertTrue(released["expired"])
        self.assertTrue(released["stale"])

        again = quota.release("t1")
        self.assertFalse(again["released"])
        self.assertTrue(again["expired"])
        self.assertEqual(quota.snapshot()["active_total"], 0)

    def test_explicit_sweep_granted_queue(self):
        clock = FakeClock()
        quota = QuotaCoordinator(
            global_limit=1,
            site_limit=1,
            lease_seconds=5,
            clock=clock,
        )
        quota.request("t1", "shop")
        quota.request("t2", "shop")
        clock.advance(5)

        swept = quota.sweep_expired()
        self.assertEqual(swept["expired_count"], 1)
        self.assertEqual([item["task"] for item in swept["granted_from_queue"]], ["t2"])

    def test_expired_slot_freed_without_new_request(self):
        clock = FakeClock()
        quota = QuotaCoordinator(
            global_limit=1,
            site_limit=1,
            lease_seconds=5,
            clock=clock,
        )
        quota.request("t1", "shop")
        clock.advance(6)
        state = quota.snapshot()
        self.assertEqual(state["active_total"], 0)


class FailureTest(unittest.TestCase):
    def test_failed_task_does_not_hold_slot(self):
        quota = QuotaCoordinator(global_limit=1, site_limit=1, lease_seconds=None)
        quota.request("t1", "shop")
        queued = quota.request("t2", "shop")
        self.assertTrue(queued["queued"])

        failed = quota.fail("t1")
        self.assertTrue(failed["failed"])
        self.assertEqual(failed["released"], 1)
        # 失败任务释放名额后，排队任务立刻补位。
        self.assertEqual([item["task"] for item in quota.snapshot()["active"]], ["t2"])
        self.assertNotIn("t1", [item["task"] for item in quota.snapshot()["active"]])

    def test_failed_queued_task_is_cancelled(self):
        quota = QuotaCoordinator(global_limit=1, site_limit=1, lease_seconds=None)
        quota.request("holder", "shop")
        quota.request("waiting", "shop")
        quota.fail("waiting")
        self.assertEqual(quota.snapshot()["queued_total"], 0)

    def test_failed_task_can_retry_later(self):
        quota = QuotaCoordinator(global_limit=1, site_limit=1, lease_seconds=None)
        quota.request("t1", "shop")
        quota.fail("t1")
        retried = quota.request("t1", "shop")
        self.assertTrue(retried["granted"])
        self.assertEqual(retried["round"], 1)


class CrossProcessStateTest(unittest.TestCase):
    def test_state_file_visible_from_other_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "quota.json")
            quota = QuotaCoordinator(
                global_limit=2,
                site_limit=1,
                lease_seconds=None,
                state_path=path,
            )
            quota.request("t1", "shop")
            quota.request("t2", "shop")

            env = dict(os.environ)
            repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
            completed = subprocess.run(
                [sys.executable, "-m", "retryquota", path, "--json"],
                capture_output=True,
                text=True,
                env=env,
                cwd=repo_root,
                check=True,
            )
            state = json.loads(completed.stdout)
            self.assertEqual(state["global_limit"], 2)
            self.assertEqual(state["site_limit"], 1)
            self.assertEqual(len(state["active"]), 1)
            self.assertEqual(len(state["queue"]), 1)
            self.assertEqual(state["queue"][0]["task"], "t2")

            # 另一个进程持有的许可释放后，状态文件同步更新。
            other = QuotaCoordinator(
                global_limit=2,
                site_limit=1,
                lease_seconds=None,
                state_path=path,
            )
            other.release("t1")
            reloaded = QuotaCoordinator(
                global_limit=2,
                site_limit=1,
                lease_seconds=None,
                state_path=path,
            )
            self.assertEqual([item["task"] for item in reloaded.snapshot()["active"]], ["t2"])


if __name__ == "__main__":
    unittest.main()
