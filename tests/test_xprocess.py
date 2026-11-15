"""跨进程租约语义测试：互斥、过期抢占、FIFO、重入、幂等释放。"""

import multiprocessing
import os
import queue
import shutil
import tempfile
import time
import unittest

from tablelease import acquire, release, status, audit_log


def _grab_then_release(table, owner, ttl, q):
    """子进程：阻塞拿租约，拿到后通报并立即释放。"""
    acquire(table, owner, ttl)
    q.put(owner)
    release(table, owner)


class LeaseCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="tablelease-test-")
        self._old = os.environ.get("TABLELEASE_DIR")
        os.environ["TABLELEASE_DIR"] = self.dir

    def tearDown(self):
        if self._old is None:
            os.environ.pop("TABLELEASE_DIR", None)
        else:
            os.environ["TABLELEASE_DIR"] = self._old
        shutil.rmtree(self.dir, ignore_errors=True)

    def _waiters(self, table):
        return [w["owner"] for w in status(table)["waiters"]]

    def _wait_until(self, pred, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if pred():
                return True
            time.sleep(0.05)
        return False


class CrossProcessMutexTest(LeaseCase):
    def test_cross_process_mutex(self):
        ctx = multiprocessing.get_context("fork")
        q = ctx.Queue()
        acquire("orders", "parent", 10)

        p = ctx.Process(target=_grab_then_release, args=("orders", "child", 10, q))
        p.start()
        # 父进程持有期间，子进程只能排队，拿不到租约
        with self.assertRaises(queue.Empty):
            q.get(timeout=0.6)
        self.assertIn("child", self._waiters("orders"))

        release("orders", "parent")
        self.assertEqual(q.get(timeout=10), "child")
        p.join(10)
        self.assertEqual(p.exitcode, 0)
        self.assertIsNone(status("orders")["holder"])

    def test_wait_queue_fifo(self):
        ctx = multiprocessing.get_context("fork")
        q = ctx.Queue()
        acquire("orders", "parent", 10)

        p1 = ctx.Process(target=_grab_then_release, args=("orders", "c1", 10, q))
        p1.start()
        self.assertTrue(self._wait_until(lambda: "c1" in self._waiters("orders")))
        p2 = ctx.Process(target=_grab_then_release, args=("orders", "c2", 10, q))
        p2.start()
        self.assertTrue(self._wait_until(lambda: "c2" in self._waiters("orders")))
        self.assertEqual(self._waiters("orders"), ["c1", "c2"])

        release("orders", "parent")
        self.assertEqual(q.get(timeout=10), "c1")  # 先到的先拿
        self.assertEqual(q.get(timeout=10), "c2")
        p1.join(10)
        p2.join(10)


class ExpiryPreemptTest(LeaseCase):
    def test_expired_lease_preempted(self):
        acquire("orders", "etl-a", 0.2)
        time.sleep(0.3)  # 租约已过期
        r = acquire("orders", "etl-b", 5)
        self.assertTrue(r["granted"])
        self.assertEqual(r["preempted"], "etl-a")
        self.assertEqual(r["holder"], "etl-b")

        events = [e for e in audit_log() if e["event"] == "preempt"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["prev_owner"], "etl-a")
        self.assertEqual(events[0]["reason"], "expired")

    def test_dead_holder_preempted(self):
        ctx = multiprocessing.get_context("fork")
        holder = ctx.Process(target=lambda: (acquire("orders", "etl-a", 60), os._exit(0)))
        holder.start()
        holder.join()  # 进程退出，租约未释放且未过期
        r = acquire("orders", "etl-b", 5)
        self.assertTrue(r["granted"])
        self.assertEqual(r["preempted"], "etl-a")
        events = [e for e in audit_log() if e["event"] == "preempt"]
        self.assertEqual(events[0]["reason"], "holder_dead")


class ReentrantTest(LeaseCase):
    def test_reentrant_count_and_partial_release(self):
        r1 = acquire("t", "a", 5)
        self.assertFalse(r1["reentrant"])
        self.assertEqual(r1["count"], 1)

        r2 = acquire("t", "a", 5)  # 重入：计数+1，原子续约
        self.assertTrue(r2["reentrant"])
        self.assertEqual(r2["count"], 2)

        rel1 = release("t", "a")  # 计数未归零，不真正释放
        self.assertFalse(rel1["released"])
        self.assertEqual(rel1["remaining"], 1)
        self.assertEqual(status("t")["holder"]["owner"], "a")

        rel2 = release("t", "a")
        self.assertTrue(rel2["released"])
        self.assertIsNone(status("t")["holder"])


class IdempotentReleaseTest(LeaseCase):
    def test_duplicate_release_is_noop(self):
        acquire("t", "a", 5)
        self.assertTrue(release("t", "a")["released"])
        dup = release("t", "a")
        self.assertFalse(dup["released"])
        self.assertTrue(dup["duplicate"])
        self.assertIsNone(status("t")["holder"])

    def test_failed_release_retryable(self):
        acquire("t", "a", 5)
        bad = release("t", "b")  # 非持有者：失败但不改状态
        self.assertFalse(bad["released"])
        self.assertEqual(status("t")["holder"]["owner"], "a")
        # 正确持有者重试成功；失败调用没有副作用
        self.assertTrue(release("t", "a")["released"])
        self.assertTrue(release("t", "a")["duplicate"])


if __name__ == "__main__":
    unittest.main()
