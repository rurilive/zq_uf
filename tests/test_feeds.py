import unittest

from iocnorm import cleanup, export, match, normalize, restore, set_confidence
from iocnorm import feeds

FEEDS = [
    {"source": "feed-a", "value": "1.2.3.4", "expires": 8, "confidence": 60},
    {"source": "feed-b", "value": "1.2.3.4", "expires": 17, "confidence": 90},
    {"source": "feed-c", "value": "Evil.Example.COM.", "expires": 5, "confidence": 40},
    {"source": "feed-a", "value": "44d88612fea8a8f36de82e1278abb02f", "expires": 17, "confidence": 70},
]


class FeedsTest(unittest.TestCase):
    def setUp(self):
        feeds._LIBRARY.clear()
        feeds._CLEANED.clear()
        feeds._CONFIDENCE_OVERRIDE.clear()

    def test_dedupe_by_type_and_value(self):
        result = normalize(FEEDS)
        self.assertEqual(result["normalized"], 3)
        self.assertEqual(result["added"], 3)
        self.assertEqual(result["sources"], {"feed-a": 2, "feed-b": 1, "feed-c": 1})

    def test_type_detection(self):
        normalize(FEEDS)
        self.assertEqual(feeds._LIBRARY[("ipv4", "1.2.3.4")]["type"], "ipv4")
        self.assertEqual(
            feeds._LIBRARY[("md5", "44d88612fea8a8f36de82e1278abb02f")]["type"], "md5"
        )
        self.assertEqual(
            feeds._LIBRARY[("domain", "evil.example.com")]["type"], "domain"
        )

    def test_domain_trailing_dot_normalized(self):
        normalize(FEEDS)
        self.assertTrue(match("evil.example.com")["hit"])

    def test_all_sources_kept_sorted_by_confidence(self):
        normalize(FEEDS)
        result = match("1.2.3.4")
        self.assertEqual([s["source"] for s in result["sources"]], ["feed-b", "feed-a"])
        self.assertEqual(result["source"], "feed-b")
        self.assertEqual(result["expires"], 17)

    def test_conflicting_sources_listed_side_by_side(self):
        normalize([
            {"source": "feed-x", "value": "5.6.7.8", "expires": 9, "confidence": 50},
            {"source": "feed-y", "value": "5.6.7.8", "expires": 9, "confidence": 50},
        ])
        sources = match("5.6.7.8")["sources"]
        self.assertEqual({s["source"] for s in sources}, {"feed-x", "feed-y"})
        self.assertEqual(len(sources), 2)

    def test_expired_entry_does_not_hit(self):
        normalize(FEEDS)
        result = match("evil.example.com", 11)
        self.assertFalse(result["hit"])
        self.assertTrue(result["expired"])
        self.assertEqual(result["source"], "")
        # 不给 day 时仍然命中
        self.assertTrue(match("evil.example.com")["hit"])

    def test_partially_expired_sources(self):
        normalize(FEEDS)
        result = match("1.2.3.4", 11)
        self.assertTrue(result["hit"])
        self.assertEqual(result["source"], "feed-b")
        active = {s["source"]: s["active"] for s in result["sources"]}
        self.assertEqual(active, {"feed-a": False, "feed-b": True})

    def test_export_filters_by_day(self):
        normalize(FEEDS)
        self.assertEqual(export()["count"], 3)
        self.assertEqual(export(11)["count"], 2)
        values = [item["value"] for item in export(11)["items"]]
        self.assertNotIn("evil.example.com", values)

    def test_normalize_reports_expired(self):
        result = normalize(FEEDS, 11)
        self.assertEqual(result["expired"], 1)

    def test_cleanup_and_restore(self):
        normalize(FEEDS)
        self.assertEqual(cleanup(11)["cleaned"], 1)
        self.assertEqual(export()["count"], 2)
        self.assertFalse(match("evil.example.com")["hit"])
        self.assertEqual(len(feeds._CLEANED), 1)
        self.assertEqual(restore()["restored"], 1)
        self.assertEqual(export()["count"], 3)
        self.assertTrue(match("evil.example.com")["hit"])

    def test_restore_selected_values(self):
        normalize(FEEDS)
        cleanup(11)
        self.assertEqual(restore(values=["nope"])["restored"], 0)
        self.assertEqual(restore(values=["evil.example.com"])["restored"], 1)

    def test_set_confidence_reorders_sources(self):
        normalize(FEEDS)
        result = set_confidence("feed-a", 95)
        self.assertEqual(result["updated"], 2)
        self.assertEqual(match("1.2.3.4")["source"], "feed-a")
        set_confidence("feed-a", None)
        self.assertEqual(match("1.2.3.4")["source"], "feed-b")

    def test_invalid_feeds_rejected(self):
        with self.assertRaises(ValueError):
            normalize("not-a-list")
        with self.assertRaises(ValueError):
            normalize(["not-a-dict"])


if __name__ == "__main__":
    unittest.main()
