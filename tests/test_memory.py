import multiprocessing
import os
import tempfile
import unittest

from hostmemo import (
    clear,
    configure,
    events,
    get,
    remember,
    report_failure,
    report_features,
    stats,
)

PROFILE_V1 = {
    "headers": {"User-Agent": "demo/1.0"},
    "encoding": "gbk",
    "features": {"title": "v1"},
}
PROFILE_V2 = {
    "headers": {"User-Agent": "demo/2.0"},
    "encoding": "utf-8",
    "features": {"title": "v2"},
}


def _child_remember(store_path, host):
    # 模拟另一个进程：重新指向同一存储文件后写入记忆
    configure(store_path=store_path)
    remember(host, PROFILE_V1)


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = os.path.join(self.tmp.name, "store.json")
        self._saved = configure(
            store_path=self.store, ttl=3600, backoff_base=0.01
        )

    def tearDown(self):
        configure(**self._saved)
        self.tmp.cleanup()

    def test_memory_hit(self):
        remember("s", PROFILE_V1)
        result = get("s")
        self.assertTrue(result["cached"])
        self.assertEqual(result["profile"], PROFILE_V1)
        self.assertFalse(result["probe"])
        self.assertEqual(stats("s")["hits"], 1)
        self.assertEqual(stats("s")["hit_rate"], 1.0)
        self.assertIn("hit", [e["event"] for e in events("s")])

    def test_features_change_invalidates_and_reprobe(self):
        remember("s", PROFILE_V1)
        self.assertTrue(report_features("s", {"title": "v2"})["changed"])
        result = get("s")
        self.assertFalse(result["cached"])
        self.assertTrue(result["probe"])
        kinds = [(e["event"], e.get("reason")) for e in events("s")]
        self.assertIn(("invalidate", "features-changed"), kinds)
        self.assertIn(("reprobe", None), kinds)
        # 重探后写入新画像，恢复命中
        remember("s", PROFILE_V2)
        self.assertTrue(get("s")["cached"])
        self.assertEqual(get("s")["profile"], PROFILE_V2)

    def test_probe_failure_invalidates_and_backs_off(self):
        remember("s", PROFILE_V1)
        first = report_failure("s")
        result = get("s")
        self.assertFalse(result["cached"])
        self.assertFalse(result["probe"])
        self.assertGreater(result["retry_after"], 0)
        second = report_failure("s")
        self.assertGreater(second["retry_after"], first["retry_after"])
        kinds = [(e["event"], e.get("reason")) for e in events("s")]
        self.assertIn(("invalidate", "probe-failed"), kinds)

    def test_expiry(self):
        remember("s", PROFILE_V1, ttl=-1)
        result = get("s")
        self.assertFalse(result["cached"])
        self.assertTrue(result["probe"])
        kinds = [(e["event"], e.get("reason")) for e in events("s")]
        self.assertIn(("invalidate", "expired"), kinds)
        self.assertIn(("reprobe", None), kinds)

    def test_shared_store_and_version_conflict(self):
        self.assertEqual(remember("s", PROFILE_V1)["version"], 1)
        self.assertEqual(remember("s", PROFILE_V2)["version"], 2)
        stale = remember("s", PROFILE_V1, expected_version=1)
        self.assertFalse(stale["stored"])
        self.assertTrue(stale["conflict"])
        # 冲突写入被拒绝，记忆里仍是 v2
        self.assertEqual(get("s")["profile"], PROFILE_V2)
        self.assertIn("conflict", [e["event"] for e in events("s")])

    def test_multiprocess_shared_update(self):
        proc = multiprocessing.Process(
            target=_child_remember, args=(self.store, "mp")
        )
        proc.start()
        proc.join()
        self.assertEqual(proc.exitcode, 0)
        # 子进程写入的记忆，本进程直接命中
        self.assertTrue(get("mp")["cached"])
        self.assertEqual(get("mp")["profile"], PROFILE_V1)

    def test_clear(self):
        remember("s", PROFILE_V1)
        self.assertEqual(clear("s")["cleared"], 1)
        self.assertFalse(get("s")["cached"])

    def test_stats_per_host(self):
        remember("a", PROFILE_V1)
        remember("b", PROFILE_V1)
        get("a")
        get("b")
        get("b")
        get("c")  # 未记忆的站点，记一次 miss
        all_stats = stats()
        self.assertEqual(all_stats["a"]["hit_rate"], 1.0)
        self.assertEqual(all_stats["b"]["hits"], 2)
        self.assertEqual(all_stats["c"]["misses"], 1)
        self.assertEqual(all_stats["c"]["hit_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
