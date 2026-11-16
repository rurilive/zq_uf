import unittest

from iocnorm import export, match, normalize
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

    def test_dedupe_by_value(self):
        self.assertEqual(normalize(FEEDS)["normalized"], 3)

    def test_type_is_unknown(self):
        normalize(FEEDS)
        self.assertEqual(feeds._LIBRARY["1.2.3.4"]["type"], "unknown")

    def test_only_first_source_is_kept(self):
        normalize(FEEDS)
        self.assertEqual(match("1.2.3.4")["source"], "feed-a")

    def test_domain_keeps_trailing_dot(self):
        normalize(FEEDS)
        self.assertFalse(match("evil.example.com")["hit"])

    def test_expired_entry_still_hits(self):
        normalize(FEEDS)
        self.assertTrue(match("evil.example.com.", 11)["hit"])
        self.assertEqual(export(11)["count"], 3)
