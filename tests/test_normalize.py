import unittest

from crawlflow import NormalizeConfig, Scheduler, fingerprint, normalize_url


class NormalizeTest(unittest.TestCase):
    def test_tracking_params_stripped(self):
        a = "https://example.com/p?id=7&utm_source=feed&utm_medium=rss"
        b = "https://example.com/p?id=7&gclid=abc&fbclid=xyz"
        self.assertEqual(fingerprint(a), fingerprint(b))

    def test_query_order_normalized(self):
        a = "https://example.com/p?a=1&b=2&c=3"
        b = "https://example.com/p?c=3&a=1&b=2"
        self.assertEqual(normalize_url(a), normalize_url(b))

    def test_encoding_normalized(self):
        a = "https://example.com/pa%7Eth?q=a%20b"
        b = "https://EXAMPLE.com/pa~th?q=a+b"
        self.assertEqual(fingerprint(a), fingerprint(b))

    def test_fragment_and_default_port_stripped(self):
        a = "https://example.com:443/p#section-2"
        b = "https://example.com/p"
        self.assertEqual(normalize_url(a), normalize_url(b))
        self.assertEqual(
            normalize_url("http://example.com:8080/p"),
            "http://example.com:8080/p",
        )

    def test_meaningful_params_kept(self):
        self.assertNotEqual(
            fingerprint("https://example.com/p?id=1"),
            fingerprint("https://example.com/p?id=2"),
        )

    def test_blank_value_and_empty_query(self):
        self.assertEqual(
            normalize_url("https://example.com/p?flag&utm_x=1"),
            "https://example.com/p?flag=",
        )

    def test_configurable_rules(self):
        cfg = NormalizeConfig(
            drop_params=frozenset(),
            drop_prefixes=(),
            keep_params=frozenset({"id"}),
            strip_trailing_slash=True,
        )
        # 白名单：只有 id 保留，utm_source 不再特殊但也进不了白名单
        self.assertEqual(
            normalize_url("https://example.com/p/?id=1&utm_source=x&page=2", cfg),
            "https://example.com/p?id=1",
        )

    def test_scheduler_dedups_variants(self):
        s = Scheduler()
        self.assertTrue(s.add("https://example.com/a?id=1&utm_source=x"))
        self.assertFalse(s.add("https://example.com/a?id=1"))
        self.assertFalse(s.add("https://EXAMPLE.com/a?id=1#top"))
        self.assertEqual(s.pending(), 1)


if __name__ == "__main__":
    unittest.main()
