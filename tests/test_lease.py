import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import tablelease
from tablelease import (
    FenceRejected,
    FencedWriter,
    LeaseError,
    acquire,
    audit_log,
    configure,
    release,
    renew,
    status,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CRASH_DRILL = REPO_ROOT / "scripts" / "crash_drill.py"


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class LeaseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "leases.db")
        self.clock = FakeClock()
        configure(db_path=self.db_path, clock=self.clock)

    def tearDown(self):
        self.tmp.cleanup()
        configure(db_path=None, clock=time.time)


class GrantAndStatusTest(LeaseTestCase):
    def test_first_acquire_granted_with_holder_and_ttl(self):
        resp = acquire("orders", "etl-1", 30)
        self.assertTrue(resp["granted"])
        self.assertEqual(resp["holder"], "etl-1")
        self.assertEqual(resp["owner"], "etl-1")
        self.assertEqual(resp["expires_in"], 30)
        self.assertEqual(resp["wait_position"], 0)
        self.assertFalse(resp["taken_over"])
        self.assertEqual(resp["entry_count"], 1)
        self.assertIsInstance(resp["fence_token"], int)

        st = status("orders")
        self.assertEqual(st["holder"], "etl-1")
        self.assertFalse(st["expired"])
        self.assertEqual(st["expires_in"], 30)

    def test_second_owner_waits_with_position(self):
        first = acquire("orders", "etl-1", 30)
        second = acquire("orders", "etl-2", 30)
        self.assertFalse(second["granted"])
        self.assertEqual(second["holder"], "etl-1")
        self.assertEqual(second["wait_position"], 1)
        self.assertEqual(second["expires_in"], 30)

        third = acquire("orders", "etl-3", 30)
        self.assertEqual(third["wait_position"], 2)

        st = status("orders")
        self.assertEqual(st["waiters"], ["etl-2", "etl-3"])

    def test_remaining_time_decreases_with_clock(self):
        acquire("orders", "etl-1", 30)
        self.clock.advance(10)
        self.assertEqual(status("orders")["expires_in"], 20)


class ReentrantTest(LeaseTestCase):
    def test_same_owner_reenter_does_not_deadlock(self):
        first = acquire("orders", "etl-1", 30)
        again = acquire("orders", "etl-1", 30)
        self.assertTrue(again["granted"])
        self.assertEqual(again["entry_count"], 2)
        # 重入不产生新 fencing token，同一次租约内 token 保持稳定。
        self.assertEqual(again["fence_token"], first["fence_token"])
        # 其他持有者仍然进队列，重入没有破坏独占性。
        other = acquire("orders", "etl-2", 30)
        self.assertFalse(other["granted"])

    def test_release_decrements_before_closing(self):
        acquire("orders", "etl-1", 30)
        acquire("orders", "etl-1", 30)
        first_release = release("orders", "etl-1")
        self.assertTrue(first_release["released"])
        self.assertEqual(first_release["entry_count"], 1)
        self.assertEqual(status("orders")["holder"], "etl-1")

        second_release = release("orders", "etl-1")
        self.assertTrue(second_release["released"])
        self.assertEqual(second_release["entry_count"], 0)
        self.assertIsNone(status("orders")["holder"])


class IdempotentReleaseTest(LeaseTestCase):
    def test_duplicate_release_does_not_raise(self):
        acquire("t", "a", 30)
        ok = release("t", "a")
        self.assertTrue(ok["released"])
        dup = release("t", "a")
        self.assertFalse(dup["released"])
        self.assertTrue(dup["duplicate"])
        # 再来一次依然不报错。
        dup2 = release("t", "a")
        self.assertTrue(dup2["duplicate"])

    def test_non_owner_release_rejected_not_stolen(self):
        acquire("t", "a", 30)
        resp = release("t", "b")
        self.assertFalse(resp["released"])
        self.assertFalse(resp["duplicate"])
        self.assertEqual(resp["holder"], "a")
        self.assertEqual(status("t")["holder"], "a")

    def test_release_allows_immediate_reacquire(self):
        acquire("t", "a", 30)
        release("t", "a")
        resp = acquire("t", "b", 10)
        self.assertTrue(resp["granted"])
        self.assertEqual(resp["holder"], "b")

    def test_release_missing_table_is_duplicate(self):
        resp = release("never-existed", "a")
        self.assertFalse(resp["released"])
        self.assertTrue(resp["duplicate"])

    def test_waiter_release_cancels_queue_without_blocking_later_owner(self):
        """等待者放弃排队（对非自己持有的租约调 release）必须离队，
        不能变成永久阻挡后来者的幽灵队头。"""
        acquire("t", "a", 30)
        acquire("t", "w", 30)  # w 排队
        self.assertEqual(status("t")["waiters"], ["w"])
        # w 放弃等待：释放被拒（不持有），但排队记录被清掉。
        cancel = release("t", "w")
        self.assertFalse(cancel["released"])
        self.assertEqual(status("t")["waiters"], [])
        # 持有者仍然完好。
        self.assertEqual(status("t")["holder"], "a")
        release("t", "a")
        # 后来者不再被幽灵队头阻挡，立即获得租约。
        late = acquire("t", "late", 30)
        self.assertTrue(late["granted"])
        self.assertEqual(late["wait_position"], 0)


class ExpiryAndTakeoverTest(LeaseTestCase):
    def test_takeover_only_after_expiry(self):
        first = acquire("orders", "etl-1", 30)
        self.clock.advance(29)
        denied = acquire("orders", "etl-2", 30)
        self.assertFalse(denied["granted"])

        self.clock.advance(2)  # 31s > 30s ttl
        takeover = acquire("orders", "etl-2", 30)
        self.assertTrue(takeover["granted"])
        self.assertTrue(takeover["taken_over"])
        self.assertEqual(takeover["previous_owner"], "etl-1")
        self.assertEqual(takeover["holder"], "etl-2")
        self.assertGreater(takeover["fence_token"], first["fence_token"])

        events = [e["event"] for e in audit_log("orders")]
        self.assertIn("takeover", events)
        takeover_event = next(e for e in audit_log("orders")
                              if e["event"] == "takeover")
        self.assertEqual(takeover_event["detail"]["previous_owner"], "etl-1")

    def test_renew_requires_matching_owner_and_valid_lease(self):
        acquire("orders", "etl-1", 30)
        with self.assertRaises(LeaseError):
            renew("orders", "etl-2", 30)
        renewed = renew("orders", "etl-1", 60)
        self.assertEqual(renewed["expires_in"], 60)

        self.clock.advance(61)
        with self.assertRaises(LeaseError):
            renew("orders", "etl-1", 30)

    def test_release_after_expiry_is_idempotent_and_audited(self):
        acquire("orders", "etl-1", 30)
        self.clock.advance(31)
        # 原持有者释放自己已过期的租约：正常清场，不报错。
        resp = release("orders", "etl-1")
        self.assertTrue(resp["released"])
        self.assertIsNone(status("orders")["holder"])
        # 再释放一次即为重复释放，依然不报错。
        dup = release("orders", "etl-1")
        self.assertFalse(dup["released"])
        self.assertTrue(dup["duplicate"])
        events = [e["event"] for e in audit_log("orders")]
        self.assertIn("released", events)
        self.assertIn("release_duplicate", events)


class FifoOrderingTest(LeaseTestCase):
    """用真实时钟 + 真实 SQLite 写锁竞争验证到达顺序。"""

    def setUp(self):
        super().setUp()
        configure(db_path=self.db_path, clock=time.time)

    def test_blocking_waiters_served_in_arrival_order(self):
        acquire("queue", "leader", 60)
        # 顺序发起非阻塞 acquire，确定性地构造到达顺序 w1 < w2 < w3。
        for name in ("w1", "w2", "w3"):
            resp = acquire("queue", name, 60)
            self.assertFalse(resp["granted"])
        queued = status("queue")["waiters"]
        self.assertEqual(queued, ["w1", "w2", "w3"])

        granted_order = []
        errors = []
        lock = threading.Lock()

        def worker(name):
            try:
                # 已在队列中：阻塞重试会复用同一排队记录并保持队位。
                resp = acquire("queue", name, 60, block=True, timeout=15,
                               poll_interval=0.02)
                self.assertTrue(resp["granted"], msg=f"{name} 未获得租约")
                with lock:
                    granted_order.append(name)
                time.sleep(0.15)
                release("queue", name)
            except BaseException as exc:  # noqa: BLE001 - 测试线程要收集失败
                with lock:
                    errors.append((name, repr(exc)))

        threads = [threading.Thread(target=worker, args=(n,))
                   for n in queued]
        for t in threads:
            t.start()
        time.sleep(0.3)  # 三个阻塞等待者都进入轮询，但队头仍被 leader 占着
        self.assertEqual(status("queue")["waiters"], ["w1", "w2", "w3"])

        release("queue", "leader")
        for t in threads:
            t.join(timeout=20)

        self.assertEqual(errors, [])
        # 授权顺序必须与排队（到达）顺序完全一致，队头优先、不可插队。
        self.assertEqual(granted_order, ["w1", "w2", "w3"])
        self.assertIsNone(status("queue")["holder"])

    def test_blocking_acquire_times_out_and_leaves_queue(self):
        acquire("queue", "leader", 60)
        resp = acquire("queue", "late", 60, block=True, timeout=0.2,
                       poll_interval=0.02)
        self.assertFalse(resp["granted"])
        self.assertTrue(resp["timed_out"])
        self.assertNotIn("late", status("queue")["waiters"])
        release("queue", "leader")


class FencingTest(LeaseTestCase):
    def test_stale_holder_cannot_overwrite_takeover_data(self):
        writer = FencedWriter(self.db_path)
        first = acquire("orders", "etl-1", 30)
        writer.write("row-1", "A-v1", first["fence_token"])
        self.assertEqual(writer.read("row-1")["payload"], "A-v1")

        # A 崩溃，租约过期，B 接管。
        self.clock.advance(31)
        takeover = acquire("orders", "etl-2", 30)
        writer.write("row-1", "B-v2", takeover["fence_token"])

        # A 复活，带着旧 token 迟到写入 —— 必须被拒，数据保持 B-v2。
        with self.assertRaises(FenceRejected):
            writer.write("row-1", "A-STALE", first["fence_token"])
        row = writer.read("row-1")
        self.assertEqual(row["payload"], "B-v2")
        self.assertEqual(row["fence_token"], takeover["fence_token"])

    def test_same_token_rewrite_allowed_within_one_lease(self):
        writer = FencedWriter(self.db_path)
        token = acquire("orders", "etl-1", 30)["fence_token"]
        writer.write("row-1", "v1", token)
        writer.write("row-1", "v2", token)  # 同 token 不算过期
        self.assertEqual(writer.read("row-1")["payload"], "v2")


class PersistenceTest(LeaseTestCase):
    def test_state_survives_new_connection(self):
        """同库新连接（模拟另一个进程）能读到完整租约状态。"""
        acquire("orders", "etl-1", 30)
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT owner, entry_count FROM leases WHERE table_name=?",
                ("orders",),
            ).fetchone()
            self.assertEqual(row, ("etl-1", 1))
        finally:
            conn.close()


class CrossProcessCrashDrillTest(unittest.TestCase):
    """在测试库上演练真·进程崩溃：子进程硬退出不释放，父进程接管。"""

    def test_crash_takeover_keeps_data_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "crash.db")
            proc = subprocess.run(
                [sys.executable, str(CRASH_DRILL), "drill", db_path, "1"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                self.fail(
                    f"崩溃演练失败 rc={proc.returncode}\n"
                    f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
            summary = json.loads(proc.stdout)
            self.assertTrue(summary["takeover_granted"])
            self.assertEqual(summary["previous_owner"], "worker-A")
            self.assertTrue(summary["stale_write_rejected"])
            self.assertEqual(summary["final_payload"], "B-v2")
            self.assertTrue(summary["cross_process_status_visible"])
            self.assertIn("takeover", summary["audit_events"])


if __name__ == "__main__":
    unittest.main()
