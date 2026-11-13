import json
import os
import tempfile
import unittest

from fingerprint import Fingerprint, digest


class FingerprintTest(unittest.TestCase):
    def test_digest_is_url(self):
        self.assertEqual(digest({"url": "http://s/a"}), "http://s/a")

    def test_seen_tracks_keys(self):
        store = Fingerprint()
        self.assertFalse(store.seen("a"))
        self.assertTrue(store.seen("a"))

    def test_different_urls_not_seen(self):
        store = Fingerprint()
        store.seen("http://s/a")
        self.assertFalse(store.seen("http://s/b"))


ARTICLE = "今日两市高开高走，科技股领涨大盘，成交量明显放大，市场情绪持续回暖，北向资金净流入超百亿。"


class UrlNormalizationTest(unittest.TestCase):
    def test_url_variants_same_digest(self):
        base = digest({"url": "http://example.com/a?b=1&a=2"})
        variants = [
            "HTTP://EXAMPLE.COM/a?a=2&b=1",
            "http://example.com:80/a?b=1&a=2",
            "http://example.com/a?a=2&b=1&utm_source=feed&fbclid=x",
            "http://example.com/a/?b=1&a=2#section",
        ]
        for url in variants:
            self.assertEqual(digest({"url": url}), base)

    def test_url_variant_hits_first_level(self):
        store = Fingerprint()
        first = store.check({"url": "http://s/a?x=1", "content": ARTICLE})
        second = store.check({"url": "http://s/a?utm_source=t&x=1#top"})
        self.assertFalse(first.hit)
        self.assertTrue(second.hit)
        self.assertEqual(second.kind, "url")
        self.assertEqual(second.similarity, 1.0)
        self.assertIsNotNone(second.first_seen)


class ContentNearDupTest(unittest.TestCase):
    def test_punctuation_edit_is_near_dup(self):
        store = Fingerprint()
        edited = ARTICLE.replace("，", "。").replace("。", "，", 1) + "。"
        self.assertFalse(store.check({"url": "http://s/a", "content": ARTICLE}).hit)
        res = store.check({"url": "http://s/b", "content": edited})
        self.assertTrue(res.hit)
        self.assertEqual(res.kind, "content")
        self.assertGreaterEqual(res.similarity, store.threshold)

    def test_threshold_configurable(self):
        changed = ARTICLE.replace("科技股领涨大盘", "消费股领跌大盘")
        strict = Fingerprint(threshold=0.99)
        loose = Fingerprint(threshold=0.5)
        for store in (strict, loose):
            store.check({"url": "http://s/a", "content": ARTICLE})
        self.assertFalse(strict.check({"url": "http://s/b", "content": changed}).hit)
        res = loose.check({"url": "http://s/b", "content": changed})
        self.assertTrue(res.hit)
        self.assertEqual(res.kind, "content")


class PersistenceTest(unittest.TestCase):
    def test_reload_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fp.json")
            with Fingerprint(storage_path=path) as store:
                res = store.check({"url": "http://s/a", "content": ARTICLE})
                self.assertFalse(res.hit)
            self.assertTrue(os.path.exists(path))
            with Fingerprint(storage_path=path) as store:
                again = store.check({"url": "http://s/a?utm_campaign=x", "content": ARTICLE})
                self.assertTrue(again.hit)
                self.assertEqual(again.kind, "url")
                self.assertIsNotNone(again.first_seen)

    def test_batch_flush(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fp.json")
            store = Fingerprint(storage_path=path, flush_every=2)
            store.check({"url": "http://s/a", "content": ARTICLE})
            self.assertFalse(os.path.exists(path))
            store.check({"url": "http://s/b", "content": "完全不同的另一篇文章，内容毫无关联。"})
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(len(data["entries"]), 2)


class WindowEvictionTest(unittest.TestCase):
    def test_old_entries_evicted(self):
        now = [1000.0]
        store = Fingerprint(window_seconds=10, clock=lambda: now[0])
        record = {"url": "http://s/a", "content": ARTICLE}
        self.assertFalse(store.check(record).hit)
        now[0] = 1005.0
        self.assertTrue(store.check(record).hit)
        now[0] = 1015.0
        self.assertFalse(store.check(record).hit)


class StatsTest(unittest.TestCase):
    def test_per_site_hit_stats(self):
        store = Fingerprint()
        store.check({"url": "http://a.com/1", "content": ARTICLE})
        store.check({"url": "http://a.com/1?utm_source=x", "content": ARTICLE})
        store.check({"url": "http://a.com/2", "content": ARTICLE + "。"})
        store.check({"url": "http://b.com/1", "content": "另一条站点的独立新闻。"})
        stats = store.stats()
        a = stats["a.com"]
        self.assertEqual(a["checked"], 3)
        self.assertEqual(a["url_hits"], 1)
        self.assertEqual(a["content_hits"], 1)
        self.assertEqual(a["new"], 1)
        self.assertAlmostEqual(a["hit_rate"], 2 / 3)
        self.assertEqual(stats["b.com"]["new"], 1)
