import unittest

from robotscache import allowed, refresh, set_fetcher


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def fetcher(host):
            self.calls.append(host)
            return {"disallow": ["/private"]}

        set_fetcher(fetcher)

    def test_disallow_blocks(self):
        self.assertFalse(allowed("http://shop/private/1"))
        self.assertTrue(allowed("http://shop/item/1"))

    def test_every_call_fetches(self):
        allowed("http://shop/item/1")
        allowed("http://shop/item/2")
        self.assertEqual(len(self.calls), 2)

    def test_refresh_fetches_again(self):
        refresh("shop")
        self.assertEqual(self.calls, ["shop"])
