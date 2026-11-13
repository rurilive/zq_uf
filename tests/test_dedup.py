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
