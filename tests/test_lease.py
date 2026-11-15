import json
import os
import tempfile
import threading
import unittest

from crawllease import (
    LeaseManager, acquire, renew, release, takeover, status, history,
    reset_default, HELD, EXPIRED, RELEASED,
)


class FakeClock:
    """可手动推进的单调时钟，供确定性测试。"""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_manager(**kwargs):
    clock = FakeClock()
    mgr = LeaseManager(ttl=10.0, warn_before=3.0, clock=clock,
                       auto_reaper=False, **kwargs)
    return mgr, clock


class AcquireTest(unittest.TestCase):
    def setUp(self):
        self.mgr, self.clock = make_manager()

    def test_first_acquire_granted_with_owner_and_expiry(self):
        r = self.mgr.acquire("t1", "host-a")
        self.assertTrue(r["granted"])
        self.assertEqual(r["owner"], "host-a")
        self.assertEqual(r["state"], HELD)
        self.assertEqual(r["expires_in"], 10.0)
        self.assertEqual(r["version"], 1)
        self.assertFalse(r["taken_over"])

    def test_second_owner_denied_while_held(self):
        self.mgr.acquire("t1", "host-a")
        r = self.mgr.acquire("t1", "host-b")
        self.assertFalse(r["granted"])
        self.assertEqual(r["owner"], "host-a")
        self.assertEqual(r["reason"], "held by another owner")

    def test_same_owner_reacquire_is_idempotent_no_version_bump(self):
        self.mgr.acquire("t1", "host-a")
        self.clock.advance(2)
        r = self.mgr.acquire("t1", "host-a")
        self.assertTrue(r["granted"])
        self.assertEqual(r["version"], 1)
        self.assertAlmostEqual(r["expires_in"], 8.0)

    def test_acquire_unknown_task_status_is_free(self):
        r = self.mgr.status("never-seen")
        self.assertEqual(r["state"], "free")
        self.assertIsNone(r["owner"])
        self.assertEqual(r["expires_in"], 0.0)


class RenewTest(unittest.TestCase):
    def setUp(self):
        self.mgr, self.clock = make_manager()

    def test_holder_renew_extends_and_resets_warning(self):
        self.mgr.acquire("t1", "host-a")
        self.clock.advance(8)
        r = self.mgr.renew("t1", "host-a", ttl=20)
        self.assertTrue(r["renewed"])
        self.assertEqual(r["expires_in"], 20.0)
        self.assertEqual(r["ttl"], 20.0)

    def test_renew_by_other_owner_fails(self):
        self.mgr.acquire("t1", "host-a")
        r = self.mgr.renew("t1", "host-b")
        self.assertFalse(r["renewed"])
        self.assertEqual(r["reason"], "not the holder")

    def test_renew_after_expiry_fails(self):
        self.mgr.acquire("t1", "host-a")
        self.clock.advance(11)
        r = self.mgr.renew("t1", "host-a")
        self.assertFalse(r["renewed"])
        self.assertEqual(r["reason"], "lease expired")
        self.assertEqual(r["state"], EXPIRED)

    def test_renew_missing_or_released_lease_fails(self):
        r = self.mgr.renew("nope", "host-a")
        self.assertFalse(r["renewed"])
        self.assertEqual(r["reason"], "no active lease")

        self.mgr.acquire("t2", "host-a")
        self.mgr.release("t2", "host-a")
        r = self.mgr.renew("t2", "host-a")
        self.assertFalse(r["renewed"])
        self.assertEqual(r["reason"], "no active lease")


class ExpiryAndTakeoverTest(unittest.TestCase):
    def setUp(self):
        self.mgr, self.clock = make_manager()

    def test_sweep_expires_lease_and_takeover_records_previous_owner(self):
        self.mgr.acquire("t1", "host-a")
        self.clock.advance(11)
        events = self.mgr.sweep()
        # 同一轮先临期提醒、后到期
        self.assertEqual([e["event"] for e in events],
                         ["expiring", "expired"])

        st = self.mgr.status("t1")
        self.assertEqual(st["state"], EXPIRED)
        self.assertFalse(st["taken_over"])

        r = self.mgr.takeover("t1", "host-b", ttl=5)
        self.assertTrue(r["granted"])
        self.assertTrue(r["taken_over"])
        self.assertEqual(r["owner"], "host-b")
        self.assertEqual(r["previous_owner"], "host-a")
        self.assertEqual(r["version"], 2)
        self.assertEqual(r["expires_in"], 5.0)

    def test_cannot_take_over_active_lease(self):
        self.mgr.acquire("t1", "host-a")
        r = self.mgr.takeover("t1", "host-b")
        self.assertFalse(r["granted"])
        self.assertFalse(r["taken_over"])
        self.assertEqual(r["owner"], "host-a")
        self.assertEqual(r["reason"], "lease is not expired")

    def test_acquire_after_expiry_is_takeover_path(self):
        self.mgr.acquire("t1", "host-a")
        self.clock.advance(11)
        r = self.mgr.acquire("t1", "host-b")
        self.assertTrue(r["granted"])
        self.assertTrue(r["taken_over"])
        self.assertEqual(r["previous_owner"], "host-a")

    def test_takeover_history_export_and_query(self):
        self.mgr.acquire("t1", "host-a")
        self.clock.advance(11)
        self.mgr.takeover("t1", "host-b")
        # 再来一次接管链：host-b 也挂了
        self.clock.advance(99)
        self.mgr.sweep()
        self.mgr.takeover("t1", "host-c")

        records = self.mgr.takeover_records("t1")
        self.assertEqual([r["owner"] for r in records], ["host-b", "host-c"])
        self.assertEqual(records[0]["previous_owner"], "host-a")
        self.assertEqual(records[1]["previous_owner"], "host-b")

        # 导出：JSON 字符串
        text = self.mgr.export_takeovers(task="t1")
        self.assertEqual([r["owner"] for r in json.loads(text)],
                         ["host-b", "host-c"])

        # 导出：写文件并返回路径
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "takeovers.json")
            out = self.mgr.export_takeovers(path, task="t1")
            self.assertEqual(out, path)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(len(json.load(f)), 2)

    def test_history_filters_by_event_owner_and_limit(self):
        self.mgr.acquire("t1", "host-a")
        self.mgr.renew("t1", "host-a")
        self.mgr.renew("t1", "host-b")  # 失败也留痕
        evs = self.mgr.history(task="t1", event="renewed")
        self.assertEqual(len(evs), 1)
        fails = self.mgr.history(event="renew_failed", owner="host-b")
        self.assertEqual(len(fails), 1)
        self.assertEqual(len(self.mgr.history(limit=2)), 2)
        # limit 取最近 N 条，全序列是 acquired/renewed/renew_failed（seq 1-3）
        self.assertEqual([e["seq"] for e in self.mgr.history(limit=2)], [2, 3])


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.mgr, self.clock = make_manager()

    def test_release_frees_lease_and_is_reacquirable(self):
        self.mgr.acquire("t1", "host-a")
        r = self.mgr.release("t1", "host-a")
        self.assertTrue(r["released"])
        self.assertFalse(r["idempotent"])
        self.assertEqual(r["state"], RELEASED)

        again = self.mgr.acquire("t1", "host-b")
        self.assertTrue(again["granted"])
        self.assertEqual(again["owner"], "host-b")
        self.assertFalse(again["taken_over"])

    def test_duplicate_release_is_idempotent_no_error(self):
        self.mgr.acquire("t1", "host-a")
        first = self.mgr.release("t1", "host-a")
        second = self.mgr.release("t1", "host-a")
        third = self.mgr.release("t1", "host-a")
        self.assertTrue(first["released"] and second["released"] and third["released"])
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertTrue(third["idempotent"])

    def test_release_unknown_task_is_idempotent(self):
        r = self.mgr.release("ghost", "host-a")
        self.assertTrue(r["released"])
        self.assertTrue(r["idempotent"])
        self.assertEqual(r["reason"], "no active lease")

    def test_release_by_non_holder_fails_but_does_not_raise(self):
        self.mgr.acquire("t1", "host-a")
        r = self.mgr.release("t1", "host-b")
        self.assertFalse(r["released"])
        self.assertEqual(r["reason"], "not the holder")
        # 原持有者仍在
        self.assertEqual(self.mgr.status("t1")["owner"], "host-a")

    def test_release_after_expiry_is_idempotent_and_keeps_expired_state(self):
        self.mgr.acquire("t1", "host-a")
        self.clock.advance(11)
        r = self.mgr.release("t1", "host-a")
        # 过期租约释放视为幂等 no-op（不报错），但状态保持 expired，仍需接管
        self.assertTrue(r["released"])
        self.assertTrue(r["idempotent"])
        self.assertEqual(r["reason"], "lease expired")
        self.assertEqual(r["state"], EXPIRED)
        # 过期后保留最后持有者用于追溯，但状态已非 held，不可续约/直接领
        self.assertEqual(r["owner"], "host-a")
        # 过期任务只能通过接管领取
        taken = self.mgr.takeover("t1", "host-b")
        self.assertTrue(taken["taken_over"])
        self.assertEqual(taken["previous_owner"], "host-a")


class WarningTest(unittest.TestCase):
    def test_warning_callback_fires_once_and_resets_on_renew(self):
        warned = []
        mgr, clock = make_manager(on_expiring=lambda t, o, left: warned.append((t, o)))
        mgr.acquire("t1", "host-a")

        clock.advance(7.5)  # 剩余 2.5 <= warn_before 3
        mgr.sweep()
        mgr.sweep()
        self.assertEqual(len(warned), 1)
        self.assertEqual(warned[0], ("t1", "host-a"))
        self.assertTrue(mgr.status("t1")["warned"])

        mgr.renew("t1", "host-a")  # 续约重置提醒
        self.assertFalse(mgr.status("t1")["warned"])
        clock.advance(8)  # 新租约剩余 2，重新进入 3 秒提醒窗口
        mgr.sweep()
        self.assertEqual(len(warned), 2)

    def test_expired_callback_fires_on_sweep(self):
        expired = []
        mgr, clock = make_manager(on_expired=lambda t, o, left: expired.append((t, o)))
        mgr.acquire("t1", "host-a")
        clock.advance(11)
        mgr.sweep()
        self.assertEqual(expired, [("t1", "host-a")])


class FifoWaitTest(unittest.TestCase):
    """真实线程 + Condition 验证到达顺序与释放即唤醒。"""

    def test_waiters_get_lease_in_arrival_order_after_release(self):
        mgr = LeaseManager(ttl=30, auto_reaper=False)
        mgr.acquire("t1", "holder")
        order, errors = [], []
        # 逐个放行：主测试确认上一个线程已在管理器锁内入队后，才让下一个开始
        gates = [threading.Event() for _ in range(3)]
        enqueued = [threading.Event() for _ in range(3)]

        def waiter(name, gate, mark):
            try:
                gate.wait(5)
                r = mgr.acquire("t1", name, wait=True, timeout=10,
                                on_enqueue=mark.set)
                self.assertTrue(r["granted"])
                order.append(name)
                mgr.release("t1", name)  # 交给队首下一个
            except Exception as e:  # pragma: no cover - 测试失败时暴露
                errors.append(e)

        threads = []
        for name, gate, mark in zip(("w1", "w2", "w3"), gates, enqueued):
            t = threading.Thread(target=waiter, args=(name, gate, mark))
            t.start()
            threads.append(t)
            gate.set()
            self.assertTrue(mark.wait(5), f"{name} did not enqueue in time")

        self.assertEqual(mgr.status("t1")["queue_length"], 3)
        mgr.release("t1", "holder")  # 交接链 holder -> w1 -> w2 -> w3
        for t in threads:
            t.join(timeout=10)
            self.assertFalse(t.is_alive())
        self.assertEqual(order, ["w1", "w2", "w3"])
        self.assertEqual(errors, [])
        # w3 拿到后已释放，任务回到 released
        self.assertEqual(mgr.status("t1")["state"], RELEASED)
        self.assertIsNone(mgr.status("t1")["owner"])

    def test_wait_timeout_returns_denied_and_leaves_queue(self):
        mgr = LeaseManager(ttl=30, auto_reaper=False)
        mgr.acquire("t1", "holder")
        r = mgr.acquire("t1", "w1", wait=True, timeout=0.05)
        self.assertFalse(r["granted"])
        self.assertTrue(r["queued"])
        self.assertEqual(r["reason"], "wait timeout")
        self.assertEqual(mgr.status("t1")["queue_length"], 0)


class ConcurrencyTest(unittest.TestCase):
    def test_parallel_acquires_only_one_holder(self):
        mgr = LeaseManager(ttl=30, auto_reaper=False)
        winners, barrier = [], threading.Barrier(8)

        def worker(name):
            barrier.wait()
            r = mgr.acquire("hot", name)
            if r["granted"]:
                winners.append(name)

        threads = [threading.Thread(target=worker, args=(f"h{i}",))
                   for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(len(winners), 1)


class ReaperThreadTest(unittest.TestCase):
    def test_background_reaper_expires_and_allows_takeover(self):
        mgr = LeaseManager(ttl=0.3, warn_before=0.1,
                           reaper_interval=0.05)
        try:
            mgr.acquire("t1", "host-a")
            self.assertEqual(mgr.status("t1")["owner"], "host-a")
            got = threading.Event()

            def grabber():
                r = mgr.acquire("t1", "host-b", wait=True, timeout=5)
                if r["granted"] and r["taken_over"]:
                    got.set()

            t = threading.Thread(target=grabber)
            t.start()
            self.assertTrue(got.wait(3), "expired lease was not taken over")
            t.join(5)
            st = mgr.status("t1")
            self.assertEqual(st["owner"], "host-b")
            self.assertEqual(st["previous_owner"], "host-a")
        finally:
            mgr.shutdown()


class ModuleApiTest(unittest.TestCase):
    """模块级便捷 API 冒烟测试，兼容原 demo 用法。"""

    def setUp(self):
        reset_default()

    def test_module_level_flow(self):
        r = acquire("t1", "host-a", ttl=30)
        self.assertTrue(r["granted"])
        self.assertFalse(acquire("t1", "host-b")["granted"])
        self.assertTrue(renew("t1", "host-a")["renewed"])
        self.assertFalse(renew("t1", "host-b")["renewed"])
        self.assertTrue(release("t1", "host-a")["released"])
        self.assertTrue(acquire("t1", "host-b")["granted"])
        self.assertEqual(history(task="t1", event="acquired")[0]["task"], "t1")


if __name__ == "__main__":
    unittest.main()
