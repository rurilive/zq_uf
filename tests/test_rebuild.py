import json
import os
import shutil
import tempfile
import threading
import time
import unittest

import crawlindex
from crawlindex import index as idx


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="crawlindex-test-")
        crawlindex.reset(self.dir)

    def tearDown(self):
        crawlindex.reset()
        shutil.rmtree(self.dir, ignore_errors=True)


class AtomicSwitchTest(Base):
    def test_old_index_serves_until_switch(self):
        crawlindex.update("old", "v1")
        seen = {}

        def hook():
            if "during" not in seen:
                seen["during"] = (
                    crawlindex.query("old")["found"],
                    crawlindex.query("new")["found"],
                    crawlindex.progress()["state"],
                )
            return False

        result = crawlindex.rebuild(source=[("new", "v2")], should_stop=hook)
        self.assertTrue(result["switched"])
        self.assertTrue(result["validated"])
        # 重建期间旧索引继续服务，新键还不可见
        self.assertEqual(seen["during"], (True, False, "rebuilding"))
        # 切换后新索引整体生效
        self.assertFalse(crawlindex.query("old")["found"])
        self.assertEqual(crawlindex.query("new")["value"], "v2")

    def test_query_latency_not_degraded_during_rebuild(self):
        src = [("k%d" % i, i) for i in range(2000)]
        crawlindex.rebuild(source=src)

        def timed(n=2000):
            start = time.perf_counter()
            for i in range(n):
                crawlindex.query("k%d" % (i % 2000))
            return (time.perf_counter() - start) / n

        baseline = timed()
        entered = threading.Event()
        release = threading.Event()
        calls = {"n": 0}

        def gated():
            calls["n"] += 1
            if calls["n"] == 1:
                entered.set()
                release.wait(5)
            return False

        t = threading.Thread(
            target=lambda: crawlindex.rebuild(source=src, should_stop=gated))
        t.start()
        self.assertTrue(entered.wait(5))
        during = timed()
        release.set()
        t.join(10)
        self.assertLess(during, baseline * 5 + 0.001)


class IncrementalMergeTest(Base):
    def test_updates_during_rebuild_merge_by_version(self):
        crawlindex.update("keep", "old")
        done = {"v": False}

        def hook():
            if not done["v"]:
                crawlindex.update("fresh", 2)    # 源里没有的新键
                crawlindex.update("keep", "new")  # 比重建快照更高的版本
                done["v"] = True
            return False

        result = crawlindex.rebuild(
            source=[("keep", "stale"), ("other", "x")], should_stop=hook)
        self.assertTrue(result["switched"])
        # 高版本增量覆盖重建快照，新键被合入
        self.assertEqual(crawlindex.query("keep")["value"], "new")
        self.assertEqual(crawlindex.query("fresh")["value"], 2)
        self.assertEqual(crawlindex.query("other")["value"], "x")

    def test_version_monotonic(self):
        versions = [crawlindex.version()]
        versions.append(crawlindex.update("a", 1)["version"])
        crawlindex.rebuild(source=[("b", 2)])
        versions.append(crawlindex.version())
        versions.append(crawlindex.update("c", 3)["version"])
        self.assertTrue(
            all(a < b for a, b in zip(versions, versions[1:])), versions)


class ValidationTest(Base):
    def test_validation_covers_count_and_sample(self):
        src = [("k%d" % i, "v%d" % i) for i in range(40)]
        result = crawlindex.rebuild(source=src)
        v = result["validation"]
        self.assertTrue(result["validated"])
        self.assertEqual(v["entry_count"], 40)
        self.assertEqual(v["expected_count"], 40)
        self.assertGreater(v["sampled"], 0)
        self.assertEqual(v["sample_mismatches"], [])

    def test_corrupt_checkpoint_fails_validation_and_rolls_back(self):
        crawlindex.update("stable", "old")
        src = [("k%d" % i, "v%d" % i) for i in range(10)]
        calls = {"n": 0}

        def stop_after_three():
            calls["n"] += 1
            return calls["n"] > 3

        r1 = crawlindex.rebuild(source=src, should_stop=stop_after_three)
        self.assertTrue(r1["interrupted"])
        # 篡改断点内容，模拟重建产物损坏
        ckpt_path = os.path.join(self.dir, "rebuild_checkpoint.json")
        with open(ckpt_path, encoding="utf-8") as f:
            ckpt = json.load(f)
        ckpt["entries"]["k0"]["value"] = "CORRUPT"
        with open(ckpt_path, "w", encoding="utf-8") as f:
            json.dump(ckpt, f)

        with self.assertLogs("crawlindex", level="WARNING"):
            r2 = crawlindex.rebuild(source=src)
        self.assertFalse(r2["switched"])
        self.assertTrue(r2["rolled_back"])
        self.assertFalse(r2["validated"])
        self.assertIn("k0", r2["validation"]["sample_mismatches"])
        # 回退后旧索引完整可用
        self.assertEqual(crawlindex.query("stable")["value"], "old")
        self.assertFalse(crawlindex.query("k1")["found"])
        # 回退动作已写日志
        with open(os.path.join(self.dir, "rollback.log"),
                  encoding="utf-8") as f:
            self.assertIn("校验失败", f.read())


class SwitchFailureTest(Base):
    def test_switch_failure_keeps_old_index(self):
        crawlindex.update("a", 1)
        orig = idx._write_atomic

        def boom(path, obj):
            if path.endswith("index.json"):
                raise OSError("disk full")
            return orig(path, obj)

        idx._write_atomic = boom
        try:
            with self.assertLogs("crawlindex", level="WARNING"):
                result = crawlindex.rebuild(source=[("b", 2)])
        finally:
            idx._write_atomic = orig
        self.assertFalse(result["switched"])
        self.assertTrue(result["rolled_back"])
        # 旧索引保留，继续服务
        self.assertEqual(crawlindex.query("a")["value"], 1)
        self.assertFalse(crawlindex.query("b")["found"])
        with open(os.path.join(self.dir, "rollback.log"),
                  encoding="utf-8") as f:
            self.assertIn("切换失败", f.read())


class ResumeTest(Base):
    def test_interrupt_and_resume(self):
        src = [("k%d" % i, i) for i in range(50)]
        calls = {"n": 0}

        def stop():
            calls["n"] += 1
            return calls["n"] > 10

        r1 = crawlindex.rebuild(source=src, should_stop=stop)
        self.assertTrue(r1["interrupted"])
        self.assertTrue(r1["resumable"])
        p = crawlindex.progress()
        self.assertEqual(p["state"], "interrupted")
        self.assertTrue(p["resumable"])
        self.assertEqual(p["processed"], 10)

        r2 = crawlindex.rebuild(source=src)
        self.assertTrue(r2["switched"])
        self.assertEqual(r2["resumed_from"], 10)
        self.assertEqual(crawlindex.query("k49")["value"], 49)
        self.assertEqual(crawlindex.progress()["state"], "idle")


class MetadataTest(Base):
    def test_metadata_persisted_and_reloaded(self):
        crawlindex.update("x", "y")
        with open(os.path.join(self.dir, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(meta["entry_count"], 1)
        self.assertGreaterEqual(meta["version"], 1)
        crawlindex.reset(self.dir)  # 清空内存，模拟重启
        self.assertEqual(crawlindex.query("x")["value"], "y")
        self.assertGreaterEqual(crawlindex.version(), 1)


if __name__ == "__main__":
    unittest.main()
