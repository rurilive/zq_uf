import unittest

from etagcache import headers, store


class EtagTest(unittest.TestCase):
    def test_no_conditional_headers(self):
        self.assertEqual(headers("https://a/1"), {})

    def test_304_overwrites_body(self):
        store("https://a/1", {"status": 200, "body": "full"})
        store("https://a/1", {"status": 304, "body": ""})
        self.assertFalse(store("https://a/1", {"status": 304, "body": ""})["cached"])

    def test_store_never_cached(self):
        self.assertFalse(store("https://b/1", {"body": "x"})["cached"])
