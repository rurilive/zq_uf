import json
import os
import tempfile
import threading
import time
import unittest

from crawlprobe2 import (aggregate, changes, configure, probe, reset, verdict,
                         wait_idle)


class BatchTest(unittest.TestCase):
    def setUp(self):
        reset()

    def tearDown(self):
        reset()

    def test_concurrent_probe_is_faster_than_serial(self):
        def slow(site, timeout):
            time.sleep(0.3)
            return "ok", "fine"

        configure(prober=slow)
        started = time.monotonic()
        out = probe(["s1", "s2", "s3", "s4"], workers=4)
        elapsed = time.monotonic() - started
        self.assertEqual(out["ok"], 4)
        self.assertLess(elapsed, 0.3 * 3)  # 串行需要 1.2s 以上

    def test_cache_hit_avoids_reprobe(self):
        calls = []

        def counting(site, timeout):
            calls.append(site)
            return "ok", ""

        configure(prober=counting)
        self.assertEqual(probe(["a1", "a2"], workers=2)["cache_hits"], 0)
        second = probe(["a1", "a2"], workers=2)
        self.assertEqual(second["cache_hits"], 2)
        self.assertEqual(len(calls), 2)

    def test_temporary_retried_permanent_blocked(self):
        calls = {"temp": 0, "perm": 0}

        def flaky(site, timeout):
            calls[site] += 1
            if site == "temp":
                return "temporary", "HTTP 503"
            return "permanent", "HTTP 404"

        configure(prober=flaky, temp_ttl=0.05)
        out = probe(["temp", "perm"], workers=2)
        self.assertEqual(out["temporary"], 1)
        self.assertEqual(out["permanent"], 1)
        self.assertEqual(verdict("perm")["verdict"], "permanent")
        self.assertIn("404", verdict("perm")["reason"])  # 永久失败带判定依据
        time.sleep(0.1)
        out2 = probe(["temp", "perm"], workers=2)
        self.assertEqual(calls["temp"], 2)   # 临时失败到期重试
        self.assertEqual(calls["perm"], 1)   # 永久失败直接拦截
        self.assertEqual(out2["cache_hits"], 1)

    def test_persistence_and_resume(self):
        with tempfile.TemporaryDirectory() as d:
            configure(prober=lambda s, t: ("ok", "first run"), state_dir=d)
            probe(["r1", "r2"], workers=2)
            self.assertTrue(os.path.exists(os.path.join(d, "verdicts.json")))
            self.assertTrue(os.path.exists(os.path.join(d, "progress.json")))
            with open(os.path.join(d, "probes.jsonl"), encoding="utf-8") as fh:
                self.assertEqual(len(fh.readlines()), 2)  # 探测项落盘

            # 模拟进程重启：清空内存，从磁盘恢复结论，中断的批次续跑
            reset()
            calls = []

            def counting(site, timeout):
                calls.append(site)
                return "ok", ""

            configure(prober=counting, state_dir=d)
            out = probe(["r1", "r2", "r3"], workers=2)
            self.assertEqual(out["cache_hits"], 2)
            self.assertEqual(calls, ["r3"])  # 只补探未完成的站点

    def test_verdict_change_is_recorded(self):
        state = {"v": "temporary"}

        def flipping(site, timeout):
            return state["v"], "flipping"

        configure(prober=flipping, temp_ttl=0.05)
        probe(["c1"])
        state["v"] = "ok"
        time.sleep(0.1)
        probe(["c1"])
        self.assertEqual(verdict("c1")["verdict"], "ok")
        self.assertTrue(any(c["site"] == "c1" and c["old"] == "temporary"
                            and c["new"] == "ok" for c in changes()))

    def test_changes_written_to_disk(self):
        state = {"v": "ok"}

        def flipping(site, timeout):
            return state["v"], "changed"

        with tempfile.TemporaryDirectory() as d:
            configure(prober=flipping, state_dir=d, ttl=0.05)
            probe(["d1"])
            state["v"] = "permanent"
            time.sleep(0.1)
            probe(["d1"])  # 过期：旧结论先返回，后台刷新发现变更
            self.assertTrue(wait_idle(5))
            with open(os.path.join(d, "changes.jsonl"), encoding="utf-8") as fh:
                rec = json.loads(fh.readline())
            self.assertEqual(rec["old"], "ok")
            self.assertEqual(rec["new"], "permanent")
            self.assertEqual(rec["reason"], "changed")

    def test_expired_cache_refreshes_in_background(self):
        calls = []

        def counting(site, timeout):
            calls.append(site)
            return "ok", "v%d" % len(calls)

        configure(prober=counting, ttl=0.05)
        probe(["b1"])
        time.sleep(0.1)
        out = probe(["b1"])  # 过期：命中旧结论并触发后台刷新
        self.assertEqual(out["cache_hits"], 1)
        self.assertTrue(out["results"][0]["stale"])
        self.assertTrue(wait_idle(5))
        self.assertEqual(len(calls), 2)

    def test_single_site_timeout_does_not_block_batch(self):
        release = threading.Event()

        def mixed(site, timeout):
            if site == "slow":
                release.wait(5)
            return "ok", ""

        configure(prober=mixed, timeout=0.2)
        started = time.monotonic()
        try:
            out = probe(["slow", "f1", "f2"], workers=3)
        finally:
            release.set()
        self.assertLess(time.monotonic() - started, 3)
        slow_row = [r for r in out["results"] if r["site"] == "slow"][0]
        self.assertEqual(slow_row["verdict"], "temporary")
        self.assertIn("timeout", slow_row["reason"])
        self.assertEqual(out["ok"], 2)

    def test_aggregate_by_domain(self):
        configure(prober=lambda s, t: ("ok", ""))
        out = probe(["http://a.example.com/x", "a.example.com",
                     "b.example.com"], workers=3)
        agg = aggregate(out)
        self.assertEqual(agg["a.example.com"]["total"], 2)
        self.assertEqual(agg["b.example.com"]["ok"], 1)


if __name__ == "__main__":
    unittest.main()
