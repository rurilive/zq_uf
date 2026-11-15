import multiprocessing
import os
import shutil
import tempfile
import unittest

from extractcache import compile, invalidate, stats


class CacheTest(unittest.TestCase):
    def test_compile_is_never_cached(self):
        compile(["a"])
        self.assertFalse(compile(["a"])["cached"])

    def test_compile_counter_grows(self):
        first = compile(["b"])["compiled"]
        self.assertEqual(compile(["b"])["compiled"], first + 1)

    def test_invalidate_hits_nothing(self):
        self.assertEqual(invalidate("v1")["invalidated"], 0)


def _child_check(home, rules, queue):
    os.environ["EXTRACTCACHE_HOME"] = home
    from extractcache import compile as child_compile

    queue.put(child_compile(rules)["cached"])


class VersionedCacheTest(unittest.TestCase):
    """带版本规则的缓存行为；每个用例独立缓存目录，互不影响。"""

    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="extractcache-test-")
        self._old_home = os.environ.get("EXTRACTCACHE_HOME")
        self._old_cap = os.environ.get("EXTRACTCACHE_CAPACITY")
        os.environ["EXTRACTCACHE_HOME"] = self._home
        os.environ.pop("EXTRACTCACHE_CAPACITY", None)

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("EXTRACTCACHE_HOME", None)
        else:
            os.environ["EXTRACTCACHE_HOME"] = self._old_home
        if self._old_cap is None:
            os.environ.pop("EXTRACTCACHE_CAPACITY", None)
        else:
            os.environ["EXTRACTCACHE_CAPACITY"] = self._old_cap
        shutil.rmtree(self._home, ignore_errors=True)

    @staticmethod
    def _rules(version, n=1, prefix="rule"):
        return [
            {"id": "%s%d" % (prefix, i), "version": version, "params": {"i": i}}
            for i in range(n)
        ]

    def test_cache_hit(self):
        rules = self._rules("v1")
        first = compile(rules)
        self.assertFalse(first["cached"])
        second = compile(rules)
        self.assertTrue(second["cached"])
        # 命中不重新编译，编译计数不变
        self.assertEqual(second["compiled"], first["compiled"])

    def test_params_change_is_new_fingerprint(self):
        compile(self._rules("v1"))
        changed = [{"id": "rule0", "version": "v1", "params": {"i": 999}}]
        self.assertFalse(compile(changed)["cached"])

    def test_version_invalidation(self):
        rules = self._rules("v2")
        compile(rules)
        self.assertEqual(invalidate("v2")["invalidated"], 1)
        self.assertFalse(compile(rules)["cached"])

    def test_capacity_eviction(self):
        os.environ["EXTRACTCACHE_CAPACITY"] = "2"
        r1 = self._rules("v1", prefix="a")
        r2 = self._rules("v1", prefix="b")
        r3 = self._rules("v1", prefix="c")
        compile(r1)
        compile(r2)
        self.assertTrue(compile(r1)["cached"])  # r1 变新且有命中
        compile(r3)  # 超容量，淘汰最久未用的 r2
        self.assertTrue(compile(r3)["cached"])  # r3 仍在缓存
        self.assertFalse(compile(r2)["cached"])  # r2 已被淘汰
        self.assertGreaterEqual(stats()["evictions"], 1)

    def test_compile_failure_not_cached(self):
        with self.assertRaises(ValueError):
            compile([{"params": {}}])  # 缺 id，编译失败
        self.assertEqual(stats()["size"], 0)

    def test_shared_across_processes(self):
        rules = self._rules("v9")
        compile(rules)
        ctx = multiprocessing.get_context("fork")
        queue = ctx.Queue()
        proc = ctx.Process(target=_child_check, args=(self._home, rules, queue))
        proc.start()
        proc.join(10)
        self.assertEqual(proc.exitcode, 0)
        self.assertTrue(queue.get_nowait())  # 子进程直接命中父进程写入的缓存

    def test_invalidation_broadcast_reaches_other_process(self):
        rules = self._rules("v10")
        compile(rules)
        invalidate("v10")
        ctx = multiprocessing.get_context("fork")
        queue = ctx.Queue()
        proc = ctx.Process(target=_child_check, args=(self._home, rules, queue))
        proc.start()
        proc.join(10)
        self.assertEqual(proc.exitcode, 0)
        self.assertFalse(queue.get_nowait())  # 子进程看到失效广播，重新编译

    def test_stats_hit_rate_per_rule(self):
        rules = self._rules("v5")
        compile(rules)
        compile(rules)
        s = stats()
        self.assertEqual(s["hits"], 1)
        self.assertEqual(s["misses"], 1)
        self.assertAlmostEqual(s["hit_rate"], 0.5)
        self.assertAlmostEqual(s["per_rule"]["rule0"]["hit_rate"], 0.5)
