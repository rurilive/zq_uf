import unittest

from reqcache import clear, get, put, stats


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
