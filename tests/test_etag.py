import unittest
from unittest import mock

from etagcache import clear, configure, fingerprint, headers, stats, store


def _response(status=200, body="", etag=None, last_modified=None, headers=None):
    response = {"status": status, "body": body}
    if etag is not None:
        response["etag"] = etag
    if last_modified is not None:
        response["last_modified"] = last_modified
    if headers is not None:
        response["headers"] = headers
    return response


class EtagTest(unittest.TestCase):
    def setUp(self):
        clear()
        configure(max_entries=500, max_entries_per_site=100)

    def test_no_conditional_headers(self):
        self.assertEqual(headers("https://a/1"), {})

    def test_304_overwrites_body(self):
        store("https://a/1", {"status": 200, "body": "full"})
        store("https://a/1", {"status": 304, "body": ""})
        self.assertFalse(store("https://a/1", {"status": 304, "body": ""})["cached"])

    def test_store_never_cached(self):
        self.assertFalse(store("https://b/1", {"body": "x"})["cached"])


class ConditionalHeadersTest(unittest.TestCase):
    def setUp(self):
        clear()
        configure(max_entries=500, max_entries_per_site=100)

    def test_headers_empty_without_cache(self):
        self.assertEqual(headers("https://shop/p"), {})

    def test_headers_carry_both_validators(self):
        store("https://shop/p", _response(
            body="full",
            etag='W/"v1"',
            last_modified="Wed, 21 Oct 2026 07:28:00 GMT",
        ))
        self.assertEqual(
            headers("https://shop/p"),
            {
                "If-None-Match": 'W/"v1"',
                "If-Modified-Since": "Wed, 21 Oct 2026 07:28:00 GMT",
            },
        )

    def test_headers_carry_single_validator(self):
        store("https://shop/p", _response(body="full", etag='"v1"'))
        self.assertEqual(headers("https://shop/p"), {"If-None-Match": '"v1"'})

    def test_headers_read_lowercase_header_map(self):
        store("https://shop/p", _response(
            body="full",
            headers={"ETag": '"v2"', "Last-Modified": "Wed, 21 Oct 2026 07:28:00 GMT"},
        ))
        self.assertEqual(headers("https://shop/p"), {
            "If-None-Match": '"v2"',
            "If-Modified-Since": "Wed, 21 Oct 2026 07:28:00 GMT",
        })

    def test_headers_empty_without_validators(self):
        store("https://shop/p", _response(body="full"))
        self.assertEqual(headers("https://shop/p"), {})


class NotModifiedTest(unittest.TestCase):
    def setUp(self):
        clear()
        configure(max_entries=500, max_entries_per_site=100)

    def test_304_keeps_content_and_refreshes_fetch_time(self):
        with mock.patch("etagcache.etag.time.time", side_effect=[100.0, 200.0]):
            store("https://shop/p", _response(body="full page", etag='"v1"'))
            result = store("https://shop/p", _response(status=304, body=""))

        self.assertTrue(result["hit"])
        self.assertTrue(result["not_modified"])
        self.assertFalse(result["updated"])
        self.assertFalse(result["cached"])
        self.assertEqual(result["body"], "full page")
        self.assertEqual(result["fetched_at"], 200.0)

    def test_304_preserves_fingerprint(self):
        store("https://shop/p", _response(body="full page", etag='"v1"'))
        digest = fingerprint("full page")
        store("https://shop/p", _response(status=304, body=""))
        # 内容未变：再存一次相同正文不算更新
        result = store("https://shop/p", _response(body="full page", etag='"v1"'))
        self.assertFalse(result["updated"])
        self.assertEqual(stats()["updates"], 1)
        self.assertEqual(stats()["hits"], 1)
        self.assertNotEqual(digest, fingerprint("other"))

    def test_304_learns_new_validators(self):
        store("https://shop/p", _response(body="full", etag='"v1"'))
        store("https://shop/p", _response(
            status=304, etag='"v2"',
            last_modified="Wed, 21 Oct 2026 07:28:00 GMT",
        ))
        self.assertEqual(headers("https://shop/p"), {
            "If-None-Match": '"v2"',
            "If-Modified-Since": "Wed, 21 Oct 2026 07:28:00 GMT",
        })

    def test_304_without_cache_is_a_miss(self):
        result = store("https://shop/p", _response(status=304, body=""))
        self.assertFalse(result["hit"])
        self.assertFalse(result["stored"])
        self.assertIsNone(result["body"])
        self.assertEqual(stats()["not_modified"], 1)

    def test_error_status_keeps_cached_content(self):
        store("https://shop/p", _response(body="full", etag='"v1"'))
        result = store("https://shop/p", _response(status=500, body="oops"))
        self.assertEqual(result["body"], "full")
        self.assertFalse(result["updated"])
        self.assertEqual(stats()["updates"], 1)

    def test_changed_200_counts_as_update(self):
        store("https://shop/p", _response(body="v1", etag='"v1"'))
        result = store("https://shop/p", _response(body="v2", etag='"v2"'))
        self.assertTrue(result["updated"])
        self.assertEqual(result["body"], "v2")
        self.assertEqual(stats()["updates"], 2)


class CapacityTest(unittest.TestCase):
    def setUp(self):
        clear()
        configure(max_entries=500, max_entries_per_site=100)

    def test_per_site_limit_evicts_oldest(self):
        configure(max_entries_per_site=2)
        store("https://shop/a", _response(body="a", etag='"a"'))
        store("https://shop/b", _response(body="b", etag='"b"'))
        result = store("https://shop/c", _response(body="c", etag='"c"'))

        self.assertEqual(result["evicted"], 1)
        self.assertEqual(stats()["sites"], {"shop": 2})
        # a 最久未用，已被淘汰；304 对它是未命中
        self.assertFalse(store("https://shop/a", _response(status=304))["hit"])
        self.assertTrue(store("https://shop/c", _response(status=304))["hit"])

    def test_lru_access_protects_entry(self):
        configure(max_entries_per_site=2)
        store("https://shop/a", _response(body="a", etag='"a"'))
        store("https://shop/b", _response(body="b", etag='"b"'))
        headers("https://shop/a")  # a 变最近使用
        store("https://shop/c", _response(body="c", etag='"c"'))

        self.assertTrue(store("https://shop/a", _response(status=304))["hit"])
        self.assertFalse(store("https://shop/b", _response(status=304))["hit"])

    def test_limit_is_per_site(self):
        configure(max_entries_per_site=1)
        store("https://one/x", _response(body="x", etag='"x"'))
        result = store("https://two/y", _response(body="y", etag='"y"'))

        self.assertEqual(result["evicted"], 0)
        self.assertEqual(stats()["sites"], {"one": 1, "two": 1})

    def test_total_limit_evicts_across_sites(self):
        configure(max_entries=2, max_entries_per_site=10)
        store("https://one/x", _response(body="x", etag='"x"'))
        store("https://two/y", _response(body="y", etag='"y"'))
        result = store("https://three/z", _response(body="z", etag='"z"'))

        self.assertEqual(result["evicted"], 1)
        self.assertEqual(stats()["sites"], {"two": 1, "three": 1})

    def test_touch_on_headers_changes_global_order(self):
        configure(max_entries=2, max_entries_per_site=10)
        store("https://one/x", _response(body="x", etag='"x"'))
        store("https://two/y", _response(body="y", etag='"y"'))
        headers("https://one/x")
        store("https://three/z", _response(body="z", etag='"z"'))

        self.assertTrue(store("https://one/x", _response(status=304))["hit"])
        self.assertFalse(store("https://two/y", _response(status=304))["hit"])

    def test_configure_rejects_bad_limits(self):
        with self.assertRaises(ValueError):
            configure(max_entries=0)
        with self.assertRaises(ValueError):
            configure(max_entries_per_site=-1)

    def test_lowering_limit_evicts_immediately(self):
        store("https://one/a", _response(body="a"))
        store("https://one/b", _response(body="b"))
        store("https://two/c", _response(body="c"))
        result = configure(max_entries=1, max_entries_per_site=1)

        self.assertEqual(result["evicted"], 2)
        self.assertEqual(stats()["entries"], 1)
        self.assertEqual(stats()["sites"], {"two": 1})


class FingerprintTest(unittest.TestCase):
    def test_fingerprint_distinguishes_content(self):
        self.assertNotEqual(fingerprint("a"), fingerprint("b"))
        self.assertEqual(fingerprint("a"), fingerprint(b"a"))
        self.assertEqual(fingerprint(None), fingerprint(""))


if __name__ == "__main__":
    unittest.main()
