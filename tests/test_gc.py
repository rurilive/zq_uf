import json
import os
import tempfile
import threading
import time
import unittest

from partgc2 import gc, leases, pin, release, reset


class GcTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = self.tmp.name

    def run_gc(self, **kw):
        kw.setdefault("state_dir", self.state)
        return gc(kw)

    # —— 已有断言（不改） ——

    def test_gc_deletes_unlisted(self):
        result = gc({"existing": ["p1", "p2"], "keep": ["p2"],
                     "state_dir": self.state})
        self.assertEqual(result["deleted"], ["p1"])

    def test_gc_has_no_skip(self):
        self.assertEqual(gc({"existing": ["p1"], "keep": [],
                             "state_dir": self.state})["skipped"], [])

    # —— 租约 ——

    def test_pin_has_holder_and_expiry(self):
        lease = pin("p1", holder="reader-1", ttl=30)
        self.assertEqual(lease["holder"], "reader-1")
        self.assertGreater(lease["until"], time.time())

    def test_lease_protects_partition(self):
        pin("p1", holder="reader-1")
        result = self.run_gc(existing=["p1", "p2"], keep=[])
        self.assertEqual(result["deleted"], ["p2"])
        self.assertEqual([s["partition"] for s in result["skipped"]], ["p1"])
        self.assertEqual(result["skipped"][0]["holder"], "reader-1")

    def test_expired_lease_auto_released(self):
        pin("p1", ttl=0.05)
        time.sleep(0.1)
        result = self.run_gc(existing=["p1"], keep=[])
        self.assertEqual(result["deleted"], ["p1"])
        self.assertEqual(result["expired"], ["p1"])
        self.assertEqual(leases(), [])

    def test_release_allows_gc(self):
        pin("p1", holder="reader-1")
        self.assertTrue(release("p1", holder="reader-1")["released"])
        self.assertEqual(self.run_gc(existing=["p1"], keep=[])["deleted"], ["p1"])

    def test_release_rejects_wrong_holder(self):
        pin("p1", holder="reader-1")
        self.assertFalse(release("p1", holder="reader-2")["released"])
        self.assertEqual(self.run_gc(existing=["p1"], keep=[])["deleted"], [])

    # —— 并发备份：先登记再写，回收跳过未完成 ——

    def test_unfinished_backup_skipped(self):
        pin("ds/p_new", holder="backup-1", kind="backup")
        result = self.run_gc(existing=["ds/p_new", "ds/p_old"], keep=[])
        self.assertEqual(result["deleted"], ["ds/p_old"])
        skipped = result["skipped"][0]
        self.assertEqual(skipped["partition"], "ds/p_new")
        self.assertEqual(skipped["kind"], "backup")

    def test_concurrent_pin_during_gc_is_safe(self):
        started = threading.Event()
        proceed = threading.Event()

        def deleter(name):
            started.set()
            self.assertTrue(proceed.wait(5))

        def backup():
            self.assertTrue(started.wait(5))
            pin("p2", holder="backup-1", kind="backup")  # 回收进行中登记
            proceed.set()

        t = threading.Thread(target=backup)
        t.start()
        result = self.run_gc(existing=["p1", "p2"], keep=[],
                             batch_size=1, deleter=deleter)
        t.join(5)
        self.assertEqual(result["deleted"], ["p1"])
        self.assertIn("p2", [s["partition"] for s in result["skipped"]])

    # —— 分批、暂停、落盘 ——

    def test_delete_in_batches(self):
        result = self.run_gc(existing=["p%d" % i for i in range(5)],
                             keep=[], batch_size=2)
        self.assertEqual(result["batches"], 3)
        logs = [f for f in os.listdir(self.state) if f.startswith("gc-batches-")]
        self.assertEqual(len(logs), 1)
        with open(os.path.join(self.state, logs[0]), encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh]
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0]["deleted"], ["p0", "p1"])

    def test_pausable_between_batches(self):
        pause = threading.Event()

        def deleter(name):
            pause.set()  # 第一批删完后置位

        result = self.run_gc(existing=["p1", "p2", "p3"], keep=[],
                             batch_size=1, deleter=deleter, pause=pause)
        self.assertTrue(result["paused"])
        self.assertEqual(result["deleted"], ["p1"])
        self.assertEqual(result["pending"], ["p2", "p3"])

    # —— 删除失败重试并记录 ——

    def test_delete_retry_then_success(self):
        attempts = {"p1": 0}

        def deleter(name):
            attempts[name] = attempts.get(name, 0) + 1
            if name == "p1" and attempts[name] == 1:
                raise OSError("busy")

        result = self.run_gc(existing=["p1"], keep=[], deleter=deleter)
        self.assertEqual(result["deleted"], ["p1"])
        self.assertEqual(attempts["p1"], 2)
        self.assertEqual(result["failed"], [])

    def test_delete_failure_recorded(self):
        def deleter(name):
            raise OSError("stuck")

        result = self.run_gc(existing=["p1"], keep=[], deleter=deleter,
                             retries=2)
        self.assertEqual(result["deleted"], [])
        self.assertEqual(result["failed"][0]["partition"], "p1")
        self.assertEqual(result["failed"][0]["attempts"], 3)
        self.assertIn("stuck", result["failed"][0]["error"])

    # —— 目录更新与审计汇总 ——

    def test_catalog_updated_after_gc(self):
        self.run_gc(existing=["p1", "p2"], keep=["p2"])
        with open(os.path.join(self.state, "catalog.json"),
                  encoding="utf-8") as fh:
            catalog = json.load(fh)
        self.assertEqual(catalog["partitions"], ["p2"])

    def test_audit_grouped_by_dataset(self):
        pin("ds2/b", holder="reader-1")
        result = self.run_gc(existing=["ds1/a", "ds2/b", "ds2/c"], keep=[])
        self.assertEqual(result["audit"]["ds1"]["deleted"], 1)
        self.assertEqual(result["audit"]["ds2"]["deleted"], 1)
        self.assertEqual(result["audit"]["ds2"]["skipped"], 1)
        with open(os.path.join(self.state, "audit.jsonl"),
                  encoding="utf-8") as fh:
            record = json.loads(fh.readline())
        self.assertEqual(record["datasets"], result["audit"])


if __name__ == "__main__":
    unittest.main()
