import json
import os
import tempfile
import time
import unittest

from condfetch import Cache, fetch, set_cache, set_opener


class CacheTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cache = Cache(os.path.join(self.dir, "cache.json"), capacity_bytes=1024)
        set_cache(self.cache)
        self.calls = []

    def tearDown(self):
        set_cache(None)
        set_opener(None)

    def stored(self):
        with open(self.cache.path, encoding="utf-8") as fh:
            return json.load(fh)


class ConditionalRequestTest(CacheTestBase):
    def setUp(self):
        super().setUp()

        def opener(url, headers):
            self.calls.append(dict(headers))
            if headers.get("If-None-Match") == '"v1"':
                return 304, "", {"ETag": '"v1"'}
            return 200, "page-v1", {
                "ETag": '"v1"',
                "Last-Modified": "Wed, 01 Jan 2020 00:00:00 GMT",
            }

        set_opener(opener)

    def test_sends_saved_validators(self):
        fetch("http://shop/1")
        fetch("http://shop/1")
        self.assertEqual(self.calls[1].get("If-None-Match"), '"v1"')
        self.assertEqual(self.calls[1].get("If-Modified-Since"),
                         "Wed, 01 Jan 2020 00:00:00 GMT")

    def test_304_returns_cached_body(self):
        first = fetch("http://shop/1")
        second = fetch("http://shop/1")
        self.assertEqual(first, "page-v1")
        self.assertEqual(second, "page-v1")
        self.assertEqual(len(self.calls), 2)


class CacheKeyTest(CacheTestBase):
    def setUp(self):
        super().setUp()

        def opener(url, headers):
            self.calls.append(dict(headers))
            etag = '"e-%s"' % headers.get("Accept", "none")
            if headers.get("If-None-Match") == etag:
                return 304, "", {"ETag": etag}
            return 200, "body-%s" % headers.get("Accept", "none"), {"ETag": etag}

        set_opener(opener)

    def test_key_distinguishes_request_headers(self):
        self.assertEqual(fetch("http://shop/1", {"Accept": "a"}), "body-a")
        self.assertEqual(fetch("http://shop/1", {"Accept": "b"}), "body-b")
        self.assertEqual(len(self.calls), 2)

    def test_same_headers_share_entry(self):
        fetch("http://shop/1", {"Accept": "a"})
        fetch("http://shop/1", {"Accept": "b"})
        again = fetch("http://shop/1", {"Accept": "a"})
        self.assertEqual(again, "body-a")
        self.assertEqual(self.calls[2].get("If-None-Match"), '"e-a"')


class SharedCacheTest(CacheTestBase):
    def test_two_caches_share_one_file(self):
        def opener_one(url, headers):
            return 200, "shared-body", {"ETag": '"shared"'}

        set_opener(opener_one)
        self.assertEqual(fetch("http://shop/1"), "shared-body")

        # 模拟另一个进程打开同一缓存文件。
        set_cache(Cache(self.cache.path, capacity_bytes=1024))
        seen = []

        def opener_two(url, headers):
            seen.append(dict(headers))
            if headers.get("If-None-Match") == '"shared"':
                return 304, "", {"ETag": '"shared"'}
            return 200, "other-body", {}

        set_opener(opener_two)
        self.assertEqual(fetch("http://shop/1"), "shared-body")
        self.assertEqual(seen[0].get("If-None-Match"), '"shared"')


class CapacityTest(CacheTestBase):
    def test_evicts_oldest_by_bytes(self):
        set_cache(Cache(self.cache.path, capacity_bytes=12))
        bodies = {"http://shop/1": "aaaaa", "http://shop/2": "bbbbb",
                  "http://shop/3": "ccccc"}

        def opener(url, headers):
            return 200, bodies[url], {}

        set_opener(opener)
        for url in bodies:
            fetch(url)
        urls = sorted(entry["url"] for entry in self.stored().values())
        self.assertEqual(urls, ["http://shop/2", "http://shop/3"])


class FreshnessTest(CacheTestBase):
    def test_fresh_entry_served_without_request(self):
        def opener(url, headers):
            self.calls.append(dict(headers))
            return 200, "fresh", {"Cache-Control": "max-age=60"}

        set_opener(opener)
        self.assertEqual(fetch("http://shop/1"), "fresh")
        self.assertEqual(fetch("http://shop/1"), "fresh")
        self.assertEqual(len(self.calls), 1)


class BackgroundRefreshTest(CacheTestBase):
    def setUp(self):
        super().setUp()
        self.count = 0

        def opener(url, headers):
            self.count += 1
            return 200, "v%d" % self.count, {
                "ETag": '"%d"' % self.count,
                "Cache-Control": "max-age=0",
            }

        set_opener(opener)

    def _stored_body(self):
        entries = self.stored()
        return next(iter(entries.values()))["body"] if entries else None

    def test_stale_returned_then_background_refresh(self):
        self.assertEqual(fetch("http://shop/1"), "v1")
        self.assertEqual(fetch("http://shop/1"), "v1")
        deadline = time.time() + 2
        while time.time() < deadline and self._stored_body() != "v2":
            time.sleep(0.02)
        self.assertEqual(self._stored_body(), "v2")
        self.assertEqual(fetch("http://shop/1"), "v2")


if __name__ == "__main__":
    unittest.main()
