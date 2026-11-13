import unittest

from condfetch import fetch, set_opener


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def opener(url, headers):
            self.calls.append((url, dict(headers)))
            return 200, "body-%d" % len(self.calls), {"ETag": '"v1"'}

        set_opener(opener)

    def test_returns_body(self):
        self.assertEqual(fetch("http://shop/1"), "body-1")

    def test_every_call_hits_server(self):
        fetch("http://shop/1")
        fetch("http://shop/1")
        self.assertEqual(len(self.calls), 2)

    def test_no_cache_reuse_between_calls(self):
        first = fetch("http://shop/1", {"If-None-Match": '"v1"'})
        second = fetch("http://shop/1", {"If-None-Match": '"v1"'})
        self.assertNotEqual(first, second)
