import unittest

from datasetcache import TTL, clear, get, invalidate, put, stats


class CacheTest(unittest.TestCase):
    def setUp(self):
        clear()

    def test_ttl_expiry(self):
        put("k", "v", now=100)
        self.assertEqual(get("k", now=100 + TTL - 1), "v")
        self.assertIsNone(get("k", now=100 + TTL))

    def test_invalidate_clears_everything(self):
        put("a", 1, now=100)
        put("b", 2, now=100)
        self.assertEqual(invalidate("orders"), 2)

    def test_no_version_check(self):
        put("k", "v", now=100)
        self.assertEqual(get("k", now=101), "v")
        self.assertEqual(set(stats().keys()), {"size", "misses", "invalidated"})
