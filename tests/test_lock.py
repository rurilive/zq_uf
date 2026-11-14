import multiprocessing
import time
import unittest

from tablelock import acquire, release, status
from tablelock import lock


def _child_acquire(table, owner, ttl, queue):
    queue.put(acquire(table, owner, ttl)["ok"])


class LockTest(unittest.TestCase):
    def setUp(self):
        lock._locks.clear()

    def test_acquire_ok(self):
        self.assertTrue(acquire("t", "a", 10)["ok"])

    def test_second_blocked(self):
        acquire("t", "a", 10)
        self.assertFalse(acquire("t", "b", 10)["ok"])

    def test_release_only_owner(self):
        acquire("t", "a", 10)
        self.assertFalse(release("t", "b"))


class CrossProcessTest(unittest.TestCase):
    def setUp(self):
        lock._locks.clear()

    def test_cross_process_mutex(self):
        acquire("t", "parent", 30)
        ctx = multiprocessing.get_context("fork")
        q = ctx.Queue()
        p = ctx.Process(target=_child_acquire, args=("t", "child", 30, q))
        p.start()
        p.join(10)
        self.assertEqual(p.exitcode, 0)
        self.assertFalse(q.get_nowait())  # 子进程拿不到父进程持有的锁
        release("t", "parent")
        p2 = ctx.Process(target=_child_acquire, args=("t", "child", 30, q))
        p2.start()
        p2.join(10)
        self.assertTrue(q.get_nowait())  # 释放后子进程能拿到


class LeaseTest(unittest.TestCase):
    def setUp(self):
        lock._locks.clear()

    def test_expired_preempted(self):
        acquire("t", "a", 0.2)
        time.sleep(0.3)
        r = acquire("t", "b", 10)
        self.assertTrue(r["ok"])
        self.assertEqual(r["preempted"]["prev_owner"], "a")
        self.assertEqual(status("t")["preemptions"][0]["new_owner"], "b")

    def test_not_expired_not_preempted(self):
        acquire("t", "a", 30)
        self.assertFalse(acquire("t", "b", 10)["ok"])
        self.assertEqual(status("t")["preemptions"], [])

    def test_reentry_counts_and_releases(self):
        acquire("t", "a", 30)
        r = acquire("t", "a", 30)
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 2)
        self.assertTrue(release("t", "a"))
        self.assertEqual(status("t")["owner"], "a")  # 计数未归零仍持有
        self.assertTrue(release("t", "a"))
        self.assertIsNone(status("t")["owner"])

    def test_renew_is_atomic_under_reentry(self):
        acquire("t", "a", 0.2)
        time.sleep(0.1)
        r = acquire("t", "a", 30)  # 重入即续期
        self.assertGreater(r["expires_at"], time.time() + 20)

    def test_release_idempotent(self):
        self.assertFalse(release("t", "a"))  # 锁不存在
        acquire("t", "a", 30)
        self.assertTrue(release("t", "a"))
        self.assertFalse(release("t", "a"))  # 重复释放不报错

    def test_release_after_preemption_is_noop(self):
        acquire("t", "a", 0.2)
        time.sleep(0.3)
        acquire("t", "b", 30)
        self.assertFalse(release("t", "a"))  # 被抢占者的释放不影响新持有者
        self.assertEqual(status("t")["owner"], "b")

    def test_wait_queue_order(self):
        acquire("t", "a", 30)
        acquire("t", "b", 30)
        acquire("t", "c", 30)
        acquire("t", "b", 30)  # 重复排队不重复记录
        self.assertEqual(status("t")["queue"], ["b", "c"])
        acquire("t", "b", 30)
        self.assertEqual(status("t")["queue"], ["b", "c"])


if __name__ == "__main__":
    unittest.main()
