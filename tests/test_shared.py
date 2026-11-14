"""跨进程共享、到期解除、任务范围、审计和异常路径的测试。

每个用例用独立临时目录作为存储（通过 SITEALLOW_STORE 环境变量）。
"""
import multiprocessing as mp
import os
import tempfile
import time
import unittest

from siteallow import StoreError, allowed, audit, block, current, unblock
from siteallow import allow


def _child_block(store, host, task):
    """子进程里拉黑站点，验证写能被其他进程看到。"""
    os.environ["SITEALLOW_STORE"] = store
    from siteallow import block as child_block
    child_block(host, "子进程拉黑", 60, task=task)


def _child_allowed(store, host, task, queue):
    """子进程里查名单，把结果送回父进程。"""
    os.environ["SITEALLOW_STORE"] = store
    from siteallow import allowed as child_allowed
    queue.put(child_allowed(host, task=task))


class SharedStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["SITEALLOW_STORE"] = os.path.join(self.tmp.name, "allowlist.json")
        self.addCleanup(os.environ.pop, "SITEALLOW_STORE", None)
        allow._blocked.clear()

    @property
    def store(self):
        return os.environ["SITEALLOW_STORE"]

    def test_cross_process_block_visible(self):
        p = mp.Process(target=_child_block, args=(self.store, "shop", None))
        p.start()
        p.join()
        self.assertEqual(p.exitcode, 0)
        self.assertFalse(allowed("shop"))  # 父进程立刻可见

    def test_cross_process_allowed_read(self):
        block("shop", "403", 60)
        queue = mp.Queue()
        p = mp.Process(target=_child_allowed, args=(self.store, "shop", None, queue))
        p.start()
        p.join()
        self.assertEqual(p.exitcode, 0)
        self.assertFalse(queue.get())  # 子进程读到一致结果

    def test_expiry_auto_unblock(self):
        block("shop", "403", 0.1)
        self.assertFalse(allowed("shop"))
        time.sleep(0.15)
        self.assertTrue(allowed("shop"))  # 到期自动解除
        self.assertEqual(current(), [])
        # 下一次写操作清除到期记录并补 expire 审计
        block("news", "429", 60)
        events = [r["event"] for r in audit(host="shop")]
        self.assertEqual(events, ["block", "expire"])

    def test_task_scope(self):
        block("shop", "403", 60, task="job-a")
        self.assertFalse(allowed("shop", task="job-a"))
        self.assertTrue(allowed("shop", task="job-b"))  # 其他任务不受影响
        self.assertTrue(allowed("shop"))  # 无任务视角只认全局记录
        block("news", "403", 60)  # 全局拉黑
        self.assertFalse(allowed("news", task="job-b"))
        self.assertFalse(allowed("news"))

    def test_unblock(self):
        block("shop", "403", 60, task="job-a")
        removed = unblock("shop", task="job-a", reason="误封")
        self.assertEqual(len(removed), 1)
        self.assertTrue(allowed("shop", task="job-a"))

    def test_audit_block_and_unblock(self):
        block("shop", "403", 60, task="job-a")
        unblock("shop", task="job-a", reason="误封")
        recs = audit(host="shop")
        self.assertEqual([r["event"] for r in recs], ["block", "unblock"])
        self.assertEqual(recs[0]["reason"], "403")
        self.assertEqual(recs[0]["task"], "job-a")
        self.assertEqual(recs[1]["reason"], "误封")
        self.assertEqual(audit(host="news"), [])
        self.assertEqual(len(audit(task="job-a")), 2)

    def test_current_lists_expiry(self):
        entry = block("shop", "403", 60)
        self.assertAlmostEqual(entry["expires_at"], entry["blocked_at"] + 60)
        cur = current()
        self.assertEqual(len(cur), 1)
        self.assertEqual(cur[0]["reason"], "403")
        self.assertEqual(cur[0]["task"], "*")
        self.assertEqual(current(task="job-a")[0]["host"], "shop")

    def test_invalid_args(self):
        with self.assertRaises(ValueError):
            block("", "403", 60)
        with self.assertRaises(ValueError):
            block("shop", "", 60)
        with self.assertRaises(ValueError):
            block("shop", "403", 0)
        with self.assertRaises(ValueError):
            block("shop", "403", -1)
        with self.assertRaises(ValueError):
            allowed("")

    def test_unblock_unknown_raises(self):
        with self.assertRaises(StoreError):
            unblock("ghost")

    def test_corrupt_store_raises(self):
        block("shop", "403", 60)
        with open(self.store, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with self.assertRaises(StoreError):
            allowed("shop")


if __name__ == "__main__":
    unittest.main()
