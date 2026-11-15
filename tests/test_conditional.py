import hashlib
import unittest

from etagcache import Cache, get, headers, reset, store


class FakeClock:
    """可注入的时钟，让抓取时间与 LRU 顺序确定。"""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        self.now += 1.0
        return self.now


def ok(body, etag=None, last_modified=None):
    response = {"status": 200, "body": body, "headers": {}}
    if etag is not None:
        response["headers"]["ETag"] = etag
    if last_modified is not None:
        response["headers"]["Last-Modified"] = last_modified
    return response


class ConditionalHeadersTest(unittest.TestCase):
    def setUp(self):
        self.cache = Cache(time_func=FakeClock())

    def test_cold_url_has_no_conditional_headers(self):
        self.assertEqual(self.cache.headers("https://a/1"), {})

    def test_etag_and_last_modified_sent_next_round(self):
        self.cache.store(
            "https://a/1",
            ok("v1", etag='"v1"', last_modified="Mon, 01 Jan 2024 00:00:00 GMT"),
        )
        self.assertEqual(
            self.cache.headers("https://a/1"),
            {
                "If-None-Match": '"v1"',
                "If-Modified-Since": "Mon, 01 Jan 2024 00:00:00 GMT",
            },
        )

    def test_header_lookup_is_case_insensitive(self):
        self.cache.store(
            "https://a/2",
            {"status": 200, "body": "x",
             "headers": {"etag": '"e"', "LAST-MODIFIED": "Tue, 02 Jan 2024 00:00:00 GMT"}},
        )
        sent = self.cache.headers("https://a/2")
        self.assertEqual(sent["If-None-Match"], '"e"')
        self.assertEqual(sent["If-Modified-Since"], "Tue, 02 Jan 2024 00:00:00 GMT")

    def test_body_without_validators_yields_no_conditional_headers(self):
        self.cache.store("https://a/3", ok("no-validators"))
        self.assertEqual(self.cache.headers("https://a/3"), {})


class NotModifiedTest(unittest.TestCase):
    def setUp(self):
        self.cache = Cache(time_func=FakeClock())

    def test_304_refreshes_fetch_time_but_keeps_body(self):
        self.cache.store(
            "https://n/1",
            ok("first page", etag='"v1"', last_modified="Mon, 01 Jan 2024 00:00:00 GMT"),
        )
        before = self.cache.get("https://n/1")

        result = self.cache.store(
            "https://n/1",
            {"status": 304, "body": "",
             "headers": {"ETag": '"v1"', "Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT"}},
        )

        after = self.cache.get("https://n/1")
        self.assertTrue(result["hit"])
        self.assertTrue(result["not_modified"])
        self.assertFalse(result["updated"])
        self.assertEqual(result["evicted"], 0)
        # 内容保持：304 的空 body 没有覆盖缓存
        self.assertEqual(after["body"], "first page")
        self.assertEqual(after["fingerprint"], before["fingerprint"])
        # 校验头原样保留，下一轮仍可条件请求
        self.assertEqual(self.cache.headers("https://n/1"),
                         {"If-None-Match": '"v1"',
                          "If-Modified-Since": "Mon, 01 Jan 2024 00:00:00 GMT"})
        # 只更新抓取时间
        self.assertGreater(after["fetched_at"], before["fetched_at"])
        self.assertEqual(after["created_at"], before["created_at"])

    def test_304_without_local_cache_is_ignored(self):
        result = self.cache.store("https://n/2", {"status": 304, "body": ""})
        self.assertFalse(result["stored"])
        self.assertFalse(result["not_modified"])
        self.assertIsNone(self.cache.get("https://n/2"))

    def test_update_after_304_replaces_content(self):
        self.cache.store("https://n/3", ok("old", etag='"v1"'))
        self.cache.store("https://n/3", {"status": 304, "body": ""})
        result = self.cache.store("https://n/3", ok("new", etag='"v2"'))
        self.assertTrue(result["updated"])
        self.assertTrue(result["changed"])
        self.assertEqual(self.cache.get("https://n/3")["body"], "new")
        self.assertEqual(self.cache.headers("https://n/3")["If-None-Match"], '"v2"')

    def test_200_same_fingerprint_is_updated_but_not_changed(self):
        self.cache.store("https://n/4", ok("same", etag='"v1"'))
        result = self.cache.store("https://n/4", ok("same", etag='"v1"'))
        self.assertTrue(result["updated"])
        self.assertFalse(result["changed"])


class CapacityEvictionTest(unittest.TestCase):
    def setUp(self):
        # 每站 2 条、全局 4 条，便于同时验证两道上限
        self.cache = Cache(max_per_site=2, max_entries=4, time_func=FakeClock())

    def test_per_site_lru_eviction(self):
        self.cache.store("https://s/1", ok("a"))
        self.cache.store("https://s/2", ok("b"))
        # 访问 /1，使 /2 成为最久未用
        self.cache.headers("https://s/1")
        result = self.cache.store("https://s/3", ok("c"))

        self.assertEqual(result["evicted"], 1)
        self.assertEqual(result["evicted_urls"], ["https://s/2"])
        self.assertIsNone(self.cache.get("https://s/2"))
        self.assertIsNotNone(self.cache.get("https://s/1"))
        self.assertIsNotNone(self.cache.get("https://s/3"))

    def test_global_lru_eviction_across_sites(self):
        self.cache.store("https://s/1", ok("a"))
        self.cache.store("https://t/1", ok("b"))
        self.cache.store("https://u/1", ok("c"))
        self.cache.store("https://v/1", ok("d"))  # 恰好 4 条
        # 刷新前面的条目，让 s/1 最久未用
        for url in ("https://t/1", "https://u/1", "https://v/1"):
            self.cache.headers(url)
        result = self.cache.store("https://w/1", ok("e"))

        self.assertEqual(result["evicted"], 1)
        self.assertEqual(result["evicted_urls"], ["https://s/1"])
        self.assertEqual(self.cache.stats()["entries"], 4)

    def test_304_refresh_lru_position(self):
        self.cache.store("https://s/1", ok("a", etag='"a"'))
        self.cache.store("https://s/2", ok("b", etag='"b"'))
        # /1 收到 304，刷新 LRU；随后 /3 入库时应淘汰 /2
        self.cache.store("https://s/1", {"status": 304, "body": ""})
        result = self.cache.store("https://s/3", ok("c"))
        self.assertEqual(result["evicted_urls"], ["https://s/2"])

    def test_site_buckets_are_independent(self):
        self.cache.store("https://s/1", ok("a"))
        self.cache.store("https://s/2", ok("b"))
        result = self.cache.store("https://t/1", ok("c"))
        self.assertEqual(result["evicted"], 0)
        self.assertEqual(self.cache.stats()["entries"], 3)

    def test_invalid_capacity_rejected(self):
        with self.assertRaises(ValueError):
            Cache(max_per_site=0, max_entries=4)
        with self.assertRaises(ValueError):
            Cache(max_per_site=2, max_entries=0)


class FingerprintTest(unittest.TestCase):
    def setUp(self):
        self.cache = Cache(time_func=FakeClock())

    def test_fingerprint_changes_with_body(self):
        self.cache.store("https://f/1", ok("abc"))
        first = self.cache.get("https://f/1")["fingerprint"]
        self.cache.store("https://f/1", ok("abc"))
        self.assertEqual(self.cache.get("https://f/1")["fingerprint"], first)
        self.cache.store("https://f/1", ok("abd"))
        self.assertNotEqual(self.cache.get("https://f/1")["fingerprint"], first)

    def test_bytes_body_fingerprinted(self):
        result = self.cache.store("https://f/2", {"status": 200, "body": b"\x00\x01"})
        self.assertTrue(result["stored"])
        self.assertEqual(
            self.cache.get("https://f/2")["fingerprint"],
            hashlib.sha256(b"\x00\x01").hexdigest(),
        )


class ModuleLevelApiTest(unittest.TestCase):
    def setUp(self):
        reset(max_per_site=2, max_entries=4)

    def test_module_level_conditional_flow(self):
        # 冷缓存无条件头
        self.assertEqual(headers("https://m/1"), {})
        store("https://m/1", {"status": 200, "body": "full",
                              "headers": {"ETag": '"v1"'}})
        self.assertEqual(headers("https://m/1"), {"If-None-Match": '"v1"'})

        result = store("https://m/1", {"status": 304, "body": ""})
        self.assertTrue(result["not_modified"])
        self.assertEqual(get("https://m/1")["body"], "full")

    def test_module_level_eviction_event(self):
        store("https://m/2", {"status": 200, "body": "a"})
        store("https://m/3", {"status": 200, "body": "b"})
        headers("https://m/2")
        result = store("https://m/4", {"status": 200, "body": "c"})
        self.assertEqual(result["evicted_urls"], ["https://m/3"])


if __name__ == "__main__":
    unittest.main()
