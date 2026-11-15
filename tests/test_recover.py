import importlib
import json
import os
import shutil
import tempfile
import unittest

from crawlrecover import mark, recover

recover_mod = importlib.import_module("crawlrecover.recover")


class RecoverTest(unittest.TestCase):
    def test_recover_starts_from_zero(self):
        mark("task-a", "page-1")
        self.assertEqual(recover("task-a")["start"], 0)

    def test_recover_skips_nothing(self):
        mark("task-a", "page-1")
        self.assertEqual(recover("task-a")["skipped"], [])

    def test_mark_echoes_page(self):
        self.assertEqual(mark("task-b", "page-9")["marked"], "page-9")


class CheckpointRecoverTest(unittest.TestCase):
    """位点恢复、半行截断、跳过已完成、幂等，全部落盘在临时目录。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="crawlrecover-test-")
        self._old_home = os.environ.get("CRAWLRECOVER_HOME")
        os.environ["CRAWLRECOVER_HOME"] = self.tmp
        self.task = "task-t"
        self.journal = os.path.join(self.tmp, self.task + ".journal")

    def tearDown(self):
        recover_mod.PROGRESS_HOOK = None
        if self._old_home is None:
            os.environ.pop("CRAWLRECOVER_HOME", None)
        else:
            os.environ["CRAWLRECOVER_HOME"] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _append_bytes(self, data):
        with open(self.journal, "ab") as f:
            f.write(data)

    def test_checkpoint_persisted_and_resumed(self):
        for p in ("p1", "p2", "p3"):
            mark(self.task, p)
        r = recover(self.task)
        pos = r["position"]
        self.assertEqual(pos["seq"], 3)
        self.assertEqual(pos["offset"], r["end"])
        self.assertTrue(pos["batch"])
        self.assertTrue(pos["ts"])
        # 位点与已完成键落盘
        with open(os.path.join(self.tmp, self.task + ".recover.json"),
                  encoding="utf-8") as f:
            state = json.load(f)
        self.assertEqual(state["position"]["seq"], 3)
        self.assertEqual(sorted(state["done"]), ["p1", "p2", "p3"])

    def test_half_line_truncated(self):
        mark(self.task, "p1")
        mark(self.task, "p2")
        size_before = os.path.getsize(self.journal)
        self._append_bytes(b'{"seq": 3, "batch": "b-cra')  # 崩溃留下的半行
        r = recover(self.task)
        self.assertTrue(r["truncated"])
        self.assertEqual(r["truncated"]["offset"], size_before)
        self.assertGreater(r["truncated"]["dropped"], 0)
        self.assertEqual(os.path.getsize(self.journal), size_before)
        self.assertEqual(r["completed"], ["p1", "p2"])
        # 截断后继续写，序号接上；后续记录重新校验通过
        self.assertEqual(mark(self.task, "p3")["seq"], 3)
        r2 = recover(self.task)
        self.assertEqual(r2["completed"], ["p1", "p2", "p3"])
        self.assertEqual(r2["position"]["seq"], 3)

    def test_bad_record_skipped_and_following_revalidated(self):
        mark(self.task, "p1")
        self._append_bytes(b'{"seq": 2, "batch": "x", "ts": "t", '
                           b'"page": "bad", "checksum": "0000"}\n')
        self.assertEqual(mark(self.task, "p2")["seq"], 2)  # 坏记录不占序号
        r = recover(self.task)
        self.assertEqual(len(r["skipped"]), 1)
        self.assertEqual(r["skipped"][0]["reason"], "bad_checksum")
        self.assertEqual(r["completed"], ["p1", "p2"])

    def test_completed_pages_skipped(self):
        mark(self.task, "p1")
        mark(self.task, "p2")
        r = recover(self.task)
        todo = [p for p in ("p1", "p2", "p3") if p not in r["completed"]]
        self.assertEqual(todo, ["p3"])
        size = os.path.getsize(self.journal)
        m = mark(self.task, "p1")  # 已完成页重复 mark 不再写日志
        self.assertTrue(m["duplicate"])
        self.assertEqual(os.path.getsize(self.journal), size)

    def test_recover_idempotent(self):
        mark(self.task, "p1")
        mark(self.task, "p2")
        r1 = recover(self.task)
        r2 = recover(self.task)
        self.assertEqual(r1, r2)
        self.assertEqual(r2["completed"], ["p1", "p2"])

    def test_batch_mismatch_resets_checkpoint(self):
        mark(self.task, "p1", batch="b1")
        r = recover(self.task, batch="b2")
        self.assertTrue(r["batch_reset"])
        self.assertEqual(r["batch"], "b2")
        self.assertEqual(r["completed"], [])
        self.assertIsNone(r["position"])
        self.assertFalse(os.path.exists(self.journal))
        self.assertTrue(os.path.exists(self.journal + ".b1.bak"))

    def test_interrupt_resumes_from_same_position(self):
        for p in ("p1", "p2", "p3"):
            mark(self.task, p)

        def boom(**kw):
            raise KeyboardInterrupt

        recover_mod.PROGRESS_HOOK = boom
        r1 = recover(self.task)
        recover_mod.PROGRESS_HOOK = None
        self.assertTrue(r1["interrupted"])
        r2 = recover(self.task)
        self.assertFalse(r2["interrupted"])
        self.assertEqual(r2["start"], r1["end"])  # 从同一位置继续
        self.assertEqual(r2["completed"], ["p1", "p2", "p3"])
        # 恢复日志保留每次尝试的起止位置
        with open(os.path.join(self.tmp, self.task + ".recoverlog"),
                  encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
        begins = [e for e in events if e["event"] == "begin"]
        dones = [e for e in events if e["event"] == "done"]
        self.assertEqual(len(begins), 2)
        self.assertTrue(all("from" in e for e in begins))
        self.assertTrue(all("from" in e and "to" in e for e in dones))
