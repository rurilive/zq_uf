import json
import os
import shutil
import tempfile
import threading
import time
import unittest

from crawlpause import (
    Coordinator,
    CrawlTask,
    TaskExecutionError,
    TaskNotPausedError,
    TaskStateError,
    is_paused,
    pause,
    resume,
)


# ---------------------------------------------------------------------- 原有断言（不改）


class PauseTest(unittest.TestCase):
    def test_pause_sets_flag(self):
        pause("t1")
        self.assertTrue(is_paused("t1"))

    def test_resume_clears_flag(self):
        pause("t1")
        resume("t1")
        self.assertFalse(is_paused("t1"))

    def test_pause_returns_task(self):
        self.assertEqual(pause("t1")["task"], "t1")


# ---------------------------------------------------------------------- 新功能测试


class CoordinatorTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="crawlpause-test-")
        self.coord = Coordinator(checkpoint_dir=self.tmp)

    def tearDown(self):
        for rec in list(self.coord._registry.values()):
            if rec.worker is not None and rec.worker.is_alive():
                if rec.stop_event is not None:
                    rec.stop_event.set()
                rec.read_only.set()
                rec.worker.join(2)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _items(self, n):
        return [{"key": "k%d" % i, "data": i} for i in range(n)]

    def _wait_until(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("condition not met within %ss" % timeout)

    # ------------------------------------------------------------ 1. 等批次收尾

    def test_pause_waits_for_batch_drain(self):
        gate = threading.Event()
        entered = threading.Event()

        def proc(item):
            entered.set()
            gate.wait(timeout=5)
            return item["data"]

        task = CrawlTask(
            "drain", self._items(4), processor=proc,
            batch_size=4, coordinator=self.coord,
        )
        task.start()
        self.assertTrue(entered.wait(5))

        done = threading.Event()
        result = {}

        def do_pause():
            result.update(self.coord.pause("drain", drain_timeout=5))
            done.set()

        p = threading.Thread(target=do_pause)
        p.start()
        # 批次还在跑：pause 必须停在 pausing 等待，而不是硬杀
        self._wait_until(
            lambda: self.coord.status("drain")["phase"] == "pausing"
        )
        time.sleep(0.1)
        self.assertFalse(done.is_set())

        gate.set()
        self.assertTrue(done.wait(5))
        p.join(5)
        self.assertFalse(p.is_alive())

        self.assertEqual(result["phase"], "paused")
        self.assertFalse(result["drain_timed_out"])
        self.assertFalse(result["interrupted"])
        self.assertIsNone(result["interrupt_reason"])
        # 批次完整收尾：4 条全部入库，检查点游标推进到批次边界 1
        self.assertEqual(task.stats()["processed"], 4)
        self.assertEqual(result["checkpoint"]["safe_cursor"], 1)
        with open(result["checkpoint"]["path"], encoding="utf-8") as fh:
            cp = json.load(fh)
        self.assertEqual(cp["safe_cursor"], 1)
        self.assertEqual(cp["task"], "drain")
        self.assertFalse(cp["interrupted"])

    # ------------------------------------------------------------ 2. 超时中断并记录

    def test_pause_timeout_interrupts_and_records(self):
        block = threading.Event()

        def proc(item):
            if item["key"] == "k2":
                block.wait(timeout=5)
            return item["data"]

        task = CrawlTask(
            "intr", self._items(4), processor=proc,
            batch_size=4, coordinator=self.coord,
        )
        task.start()
        task.wait_batch_started()
        # k0、k1 已写入，k2 卡在处理器里
        self._wait_until(lambda: task.stats()["processed"] >= 2)

        result = self.coord.pause("intr", drain_timeout=0.2)

        self.assertTrue(result["drain_timed_out"])
        self.assertTrue(result["interrupted"])
        self.assertIn("drain timeout", result["interrupt_reason"])
        self.assertEqual(result["phase"], "paused")
        # 检查点仍记批次边界（0），批次内已写的 2 条不回滚
        self.assertEqual(result["checkpoint"]["safe_cursor"], 0)
        self.assertEqual(task.stats()["processed"], 2)
        self.assertEqual(task.stats()["stored"], 2)
        with open(result["checkpoint"]["path"], encoding="utf-8") as fh:
            cp = json.load(fh)
        self.assertTrue(cp["interrupted"])
        self.assertEqual(cp["safe_cursor"], 0)

        block.set()
        task.wait_done(2)

    # ------------------------------------------------------------ 3. 从检查点恢复

    def test_resume_continues_from_checkpoint(self):
        gate = threading.Event()

        def proc(item):
            if item["key"] in ("k3", "k4", "k5"):
                gate.wait(timeout=5)
            return item["data"]

        task = CrawlTask(
            "res", self._items(6), processor=proc,
            batch_size=3, coordinator=self.coord,
        )
        task.start()
        # 第一批完整收尾，第二批卡在 k3
        self._wait_until(lambda: task.stats()["safe_cursor"] >= 1)

        presult = self.coord.pause("res", drain_timeout=0.2)
        self.assertEqual(presult["checkpoint"]["safe_cursor"], 1)
        self.assertTrue(presult["interrupted"])

        gate.set()
        task.wait_done(2)  # 旧工作线程退出到批次边界

        rresult = self.coord.resume("res")
        self.assertEqual(rresult["resumed_from_cursor"], 1)
        self.assertEqual(rresult["phase"], "running")
        task.wait_done(5)

        stats = task.stats()
        self.assertEqual(stats["phase"], "running")
        self.assertEqual(stats["safe_cursor"], 2)
        self.assertEqual(stats["stored"], 6)
        self.assertEqual(stats["processed"], 6)

    # ------------------------------------------------------------ 4. 幂等去重

    def test_idempotent_resume_skips_already_written_keys(self):
        gate = threading.Event()

        def proc(item):
            if item["key"] == "k4":  # 第二批：k3 已写，k4 卡住
                gate.wait(timeout=5)
            return item["data"]

        task = CrawlTask(
            "idem-write", self._items(6), processor=proc,
            batch_size=3, coordinator=self.coord,
        )
        task.start()
        self._wait_until(lambda: task.stats()["safe_cursor"] >= 1)
        self._wait_until(lambda: task.stats()["processed"] >= 4)

        self.coord.pause("idem-write", drain_timeout=0.2)
        gate.set()
        task.wait_done(2)

        before = task.stats()
        self.assertEqual(before["stored"], 4)  # k0..k3 已入库

        rresult = self.coord.resume("idem-write")
        self.assertEqual(rresult["duplicates_so_far"], 0)
        task.wait_done(5)

        stats = task.stats()
        self.assertEqual(stats["stored"], 6)            # 无重复入库
        self.assertEqual(stats["duplicates"], 1)        # k3 重放被幂等键挡下
        self.assertEqual(stats["processed"], 6)         # 新写入仍是 6 条
        self.assertEqual(len(set(task.store.keys())), task.store.size())

    # ------------------------------------------------------------ 5. 暂停/恢复自身幂等

    def test_pause_and_resume_calls_are_idempotent(self):
        task = CrawlTask(
            "idem-call", self._items(3),
            batch_size=3, coordinator=self.coord,
        )
        task.start()
        task.wait_done(5)

        r1 = self.coord.pause("idem-call")
        checkpoint_path = r1["checkpoint"]["path"]
        mtime = os.stat(checkpoint_path).st_mtime_ns
        r2 = self.coord.pause("idem-call")  # 重复调用
        r3 = self.coord.pause("idem-call")

        self.assertEqual(r1, r2)
        self.assertEqual(r2, r3)
        self.assertEqual(self.coord.status("idem-call")["phase"], "paused")
        # 检查点没有被重写
        self.assertEqual(os.stat(checkpoint_path).st_mtime_ns, mtime)

        rr1 = self.coord.resume("idem-call")
        rr2 = self.coord.resume("idem-call")  # 重复调用
        self.assertEqual(rr1, rr2)
        self.assertEqual(self.coord.status("idem-call")["phase"], "running")

    # ------------------------------------------------------------ 6. 多任务独立控制

    def test_tasks_are_controlled_independently(self):
        a = CrawlTask(
            "a", self._items(2), batch_size=2, coordinator=self.coord,
        )
        b = CrawlTask(
            "b", self._items(2), batch_size=2, coordinator=self.coord,
        )
        a.start()
        b.start()
        a.wait_done(5)
        b.wait_done(5)

        self.coord.pause("a")
        self.assertTrue(self.coord.is_paused("a"))
        self.assertFalse(self.coord.is_paused("b"))
        self.assertEqual(self.coord.status("b")["phase"], "running")

        self.coord.resume("a")
        self.assertFalse(self.coord.is_paused("a"))
        self.assertEqual(self.coord.status("a")["phase"], "running")

    # ------------------------------------------------------------ 7. 异常路径明确报错

    def test_resume_unknown_task_raises(self):
        with self.assertRaises(TaskNotPausedError) as ctx:
            self.coord.resume("never-seen")
        self.assertIn("never-seen", str(ctx.exception))

    def test_resume_running_task_without_pause_raises(self):
        task = CrawlTask(
            "running-x", self._items(2),
            batch_size=2, coordinator=self.coord,
        )
        task.start()
        task.wait_done(5)
        with self.assertRaises(TaskStateError) as ctx:
            self.coord.resume("running-x")
        self.assertIn("not paused", str(ctx.exception))

    def test_processor_error_surfaces_with_context(self):
        def proc(item):
            if item["key"] == "k1":
                raise RuntimeError("boom")
            return item["data"]

        task = CrawlTask(
            "fail", self._items(3), processor=proc,
            batch_size=3, coordinator=self.coord,
        )
        task.start()
        task.wait_done(5)

        status = self.coord.status("fail")
        self.assertEqual(status["phase"], "failed")
        self.assertIn("k1", status["error"])
        self.assertIn("boom", status["error"])

        with self.assertRaises(TaskStateError) as ctx:
            self.coord.pause("fail")
        self.assertIn("boom", str(ctx.exception))
        self.assertEqual(self.coord.status("fail")["phase"], "failed")

    def test_duplicate_registration_rejected(self):
        CrawlTask("dup", [], coordinator=self.coord)
        with self.assertRaises(TaskStateError):
            CrawlTask("dup", [], coordinator=self.coord)

    def test_concurrent_pause_calls_are_serialized(self):
        gate = threading.Event()

        def proc(item):
            gate.wait(timeout=5)
            return item["data"]

        task = CrawlTask(
            "concurrent", self._items(4), processor=proc,
            batch_size=4, coordinator=self.coord,
        )
        task.start()
        task.wait_batch_started()

        results = []
        threads = []
        for _ in range(2):
            t = threading.Thread(
                target=lambda: results.append(
                    self._try(lambda: self.coord.pause("concurrent", drain_timeout=5))
                )
            )
            threads.append(t)
        for t in threads:
            t.start()
        self._wait_until(
            lambda: self.coord.status("concurrent")["phase"] == "pausing"
        )
        gate.set()
        for t in threads:
            t.join(5)

        # 无论谁先谁后：两次都不抛异常、返回同一份结果，阶段只迁移一次。
        errors = [e for e in results if isinstance(e, BaseException)]
        payloads = [e for e in results if not isinstance(e, BaseException)]
        self.assertEqual(errors, [])
        self.assertEqual(payloads[0], payloads[1])
        self.assertEqual(payloads[0]["phase"], "paused")
        self.assertEqual(task.stats()["safe_cursor"], 1)
        # 恢复后也只能有一个工作线程在跑（不重复 spawn）
        self.coord.resume("concurrent")
        task.wait_done(5)
        self.assertEqual(task.stats()["stored"], 4)

    @staticmethod
    def _try(fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - 测试里收集异常
            return exc

    def test_bad_constructor_arguments_raise(self):
        with self.assertRaises(ValueError):
            CrawlTask("bad-bs", [], batch_size=0, coordinator=self.coord)
        with self.assertRaises(ValueError):
            CrawlTask("bad-item", [123], coordinator=self.coord)

    def test_checkpoint_failure_raises_checkpoint_error(self):
        from crawlpause import CheckpointError

        task = CrawlTask(
            "cp-fail", self._items(2),
            batch_size=2, coordinator=self.coord,
        )
        task.start()
        task.wait_done(5)
        # 把检查点目录变成文件，落盘必然失败
        shutil.rmtree(self.tmp)
        with open(self.tmp, "w", encoding="utf-8") as fh:
            fh.write("not-a-dir")
        with self.assertRaises(CheckpointError):
            self.coord.pause("cp-fail")


if __name__ == "__main__":
    unittest.main()
