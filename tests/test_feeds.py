import unittest

from iocnorm import (
    cleaned,
    expiring,
    export,
    match,
    normalize,
    purge,
    restore,
    set_confidence,
)
from iocnorm import feeds

FEEDS = [
    {"source": "feed-a", "value": "1.2.3.4", "expires": 8, "confidence": 60},
    {"source": "feed-b", "value": "1.2.3.4", "expires": 17, "confidence": 90},
    {"source": "feed-c", "value": "Evil.Example.COM.", "expires": 5, "confidence": 40},
    {"source": "feed-a", "value": "44D88612FEA8A8F36DE82E1278ABB02F", "expires": 17, "confidence": 70},
]


class FeedsTest(unittest.TestCase):
    def setUp(self):
        feeds._LIBRARY.clear()
        feeds._CLEANED.clear()
        feeds._CONFIDENCE_OVERRIDE.clear()

    def test_normalize_by_type(self):
        normalize(FEEDS)
        self.assertEqual(feeds._LIBRARY[("ip", "1.2.3.4")]["type"], "ip")
        self.assertEqual(feeds._LIBRARY[("domain", "evil.example.com")]["type"], "domain")
        self.assertEqual(
            feeds._LIBRARY[("hash", "44d88612fea8a8f36de82e1278abb02f")]["type"], "hash")

    def test_normalize_returns_counts_and_distribution(self):
        result = normalize(FEEDS, day=11)
        self.assertEqual(result["normalized"], 3)
        self.assertEqual(result["added"], 3)
        self.assertEqual(result["sources"], {"feed-a": 2, "feed-b": 1, "feed-c": 1})
        # 1.2.3.4 条目有效期取最晚的 17，只有 evil.example.com(5) 过期
        self.assertEqual(result["expired"], 1)

    def test_domain_trailing_dot_normalized(self):
        normalize(FEEDS)
        self.assertTrue(match("evil.example.com")["hit"])

    def test_hash_case_normalized(self):
        normalize(FEEDS)
        self.assertTrue(match("44d88612fea8a8f36de82e1278abb02f")["hit"])

    def test_all_sources_kept_sorted_by_confidence(self):
        normalize(FEEDS)
        result = match("1.2.3.4")
        self.assertEqual([s["source"] for s in result["sources"]], ["feed-b", "feed-a"])
        self.assertEqual(result["source"], "feed-b")
        self.assertEqual(result["expires"], 17)  # 取各来源最晚有效期

    def test_expired_entry_does_not_hit(self):
        normalize(FEEDS)
        result = match("evil.example.com", day=11)
        self.assertFalse(result["hit"])
        self.assertTrue(result["expired"])

    def test_expired_entry_leaves_blocklist(self):
        normalize(FEEDS)
        self.assertEqual(export()["count"], 3)
        values = [i["value"] for i in export(day=11)["items"]]
        self.assertNotIn("evil.example.com", values)
        self.assertIn("1.2.3.4", values)  # 有效期 17，仍在拦截列表

    def test_expiring_filter_by_days(self):
        normalize(FEEDS)
        # 条目有效期：1.2.3.4→17，evil.example.com→5，hash→17
        self.assertEqual(expiring(day=0, within=10)["count"], 1)  # 5
        self.assertEqual(expiring(day=8, within=10)["count"], 2)  # 17、17

    def test_purge_keeps_record_and_restore(self):
        normalize(FEEDS)
        self.assertEqual(purge(day=11)["purged"], 1)
        self.assertEqual(len(feeds._LIBRARY), 2)
        self.assertEqual(cleaned()["count"], 1)
        self.assertTrue(restore("evil.example.com")["restored"])
        self.assertEqual(len(feeds._LIBRARY), 3)
        self.assertEqual(cleaned()["count"], 0)
        self.assertFalse(restore("not-in-library.com")["restored"])

    def test_set_confidence_reorders_sources(self):
        normalize(FEEDS)
        result = set_confidence("feed-a", 95)
        self.assertEqual(result["updated"], 2)
        self.assertEqual(match("1.2.3.4")["source"], "feed-a")
        # 后续归一也沿用人工调整
        normalize([{"source": "feed-a", "value": "9.9.9.9", "expires": 3, "confidence": 10}])
        self.assertEqual(match("9.9.9.9")["sources"][0]["confidence"], 95)

    def test_same_source_merges_expires(self):
        normalize([
            {"source": "feed-a", "value": "1.2.3.4", "expires": 8, "confidence": 60},
            {"source": "feed-a", "value": "1.2.3.4", "expires": 20, "confidence": 60},
        ])
        self.assertEqual(match("1.2.3.4")["expires"], 20)
        self.assertEqual(len(match("1.2.3.4")["sources"]), 1)


if __name__ == "__main__":
    unittest.main()
