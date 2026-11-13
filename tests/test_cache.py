import os
import subprocess
import sys
import tempfile
import time
import unittest

from reqcache import clear, configure, get, make_key, put, report, stats

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CAPACITY = 64 * 1024 * 1024


class CacheTest(unittest.TestCase):
    def setUp(self):
        clear()

    def test_hit_after_put(self):
        put("http://shop/1", {"status": 200})
        self.assertEqual(get("http://shop/1")["status"], 200)

    def test_key_is_exact_string(self):
        put("http://shop/1", {"status": 200})
        self.assertIsNone(get("POST http://shop/1"))

    def test_no_capacity_limit(self):
        for index in range(50):
            put(f"http://shop/{index}", {"status": 200})
        self.assertEqual(stats()["size"], 50)

    def test_stats_counts(self):
        put("k", 1)
        get("k")
        get("missing")
        self.assertEqual(stats(), {"hits": 1, "misses": 1, "size": 1})


class KeyTest(unittest.TestCase):
    def setUp(self):
        clear()

    def test_method_parts_key(self):
        put(make_key("GET", "http://shop/1"), {"status": 200})
        self.assertIsNone(get(make_key("POST", "http://shop/1")))
        self.assertEqual(get(make_key("get", "http://shop/1"))["status"], 200)

    def test_vary_headers_part_key(self):
        put(make_key("GET", "http://shop/2", {"Accept": "application/json"}), {"status": 200})
        self.assertIsNone(get(make_key("GET", "http://shop/2", {"Accept": "text/html"})))
        self.assertIsNone(get(make_key("GET", "http://shop/2")))

    def test_non_vary_headers_ignored(self):
        first = make_key("GET", "http://shop/3", {"accept": "application/json", "User-Agent": "a"})
        second = make_key("GET", "http://shop/3", {"Accept": "application/json", "User-Agent": "b"})
        self.assertEqual(first, second)

    def test_tuple_key(self):
        put(("GET", "http://shop/4"), {"status": 200})
        self.assertEqual(get(make_key("GET", "http://shop/4"))["status"], 200)


class ExpiryTest(unittest.TestCase):
    def setUp(self):
        clear()
        self.addCleanup(configure, ttl_map={200: 300.0, 404: 30.0})

    def test_negative_cache_404(self):
        key = make_key("GET", "http://shop/missing")
        put(key, {"status": 404, "body": "not found"})
        self.assertEqual(get(key)["status"], 404)
        self.assertEqual(stats()["hits"], 1)

    def test_negative_cache_expires_fast(self):
        configure(ttl_map={404: 0.05})
        key = make_key("GET", "http://shop/missing")
        put(key, {"status": 404})
        time.sleep(0.06)
        self.assertIsNone(get(key))
        self.assertGreaterEqual(report()["expirations"], 1)

    def test_200_expires_after_ttl(self):
        configure(ttl_map={200: 0.05})
        put("http://shop/1", {"status": 200})
        self.assertIsNotNone(get("http://shop/1"))
        time.sleep(0.06)
        self.assertIsNone(get("http://shop/1"))

    def test_5xx_not_cached(self):
        put("http://shop/err", {"status": 500})
        self.assertIsNone(get("http://shop/err"))
        self.assertEqual(stats()["size"], 0)


class CapacityTest(unittest.TestCase):
    def setUp(self):
        clear()
        configure(capacity_bytes=100)
        self.addCleanup(configure, capacity_bytes=DEFAULT_CAPACITY)

    def test_lru_eviction_by_bytes(self):
        put("a", {"status": 200, "body": "x" * 60})
        put("b", {"status": 200, "body": "y" * 60})
        self.assertIsNone(get("a"))
        self.assertIsNotNone(get("b"))
        self.assertGreaterEqual(report()["evictions"], 1)

    def test_hit_refreshes_lru_order(self):
        configure(capacity_bytes=150)
        put("a", {"status": 200, "body": "x" * 60})
        put("b", {"status": 200, "body": "y" * 60})
        self.assertIsNotNone(get("a"))
        put("c", {"status": 200, "body": "z" * 60})
        self.assertIsNone(get("b"))
        self.assertIsNotNone(get("a"))
        self.assertIsNotNone(get("c"))

    def test_oversize_entry_not_cached(self):
        put("huge", {"status": 200, "body": "x" * 500})
        self.assertIsNone(get("huge"))
        self.assertEqual(stats()["size"], 0)


class SharedTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(os.environ.pop, "REQCACHE_DIR", None)
        os.environ["REQCACHE_DIR"] = self.tmpdir
        clear()

    def test_put_visible_across_processes(self):
        env = dict(os.environ, PYTHONPATH=REPO_ROOT)
        subprocess.run(
            [sys.executable, "-c",
             "from reqcache import put; put('shared', {'status': 200, 'body': 'from-child'})"],
            check=True, env=env, cwd=REPO_ROOT,
        )
        self.assertEqual(get("shared")["body"], "from-child")
        self.assertEqual(stats()["hits"], 1)

    def test_stats_shared_across_processes(self):
        put("k", {"status": 200})
        env = dict(os.environ, PYTHONPATH=REPO_ROOT)
        subprocess.run(
            [sys.executable, "-c", "from reqcache import get; get('k'); get('nope')"],
            check=True, env=env, cwd=REPO_ROOT,
        )
        self.assertEqual(stats(), {"hits": 1, "misses": 1, "size": 1})


class ReportTest(unittest.TestCase):
    def setUp(self):
        clear()

    def test_report_metrics(self):
        put("k", {"status": 200, "body": "ab"})
        get("k")
        get("missing")
        metrics = report()
        self.assertEqual(metrics["hits"], 1)
        self.assertEqual(metrics["fetches"], 1)
        self.assertAlmostEqual(metrics["hit_rate"], 0.5)
        self.assertEqual(metrics["bytes"], 2)
        self.assertEqual(metrics["evictions"], 0)
        self.assertEqual(metrics["expirations"], 0)
