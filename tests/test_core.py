import unittest

from linkguard import follow


class LinkguardTest(unittest.TestCase):
    def test_returns_response(self):
        self.assertEqual(follow("http://s/a", lambda url: {"status": 200}), {"status": 200})

    def test_passes_url(self):
        seen = {}
        follow("http://s/a", lambda url: seen.setdefault("url", url))
        self.assertEqual(seen["url"], "http://s/a")
