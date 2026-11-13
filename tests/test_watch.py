import unittest

from selectorwatch import check


class WatchTest(unittest.TestCase):
    def test_counts_non_empty(self):
        result = check("shop", [{"title": "a", "price": ""}, {"title": "b", "price": "9"}])
        self.assertEqual(result["hits"], 3)
        self.assertEqual(result["pages"], 2)

    def test_site_echoed(self):
        self.assertEqual(check("news", [])["site"], "news")

    def test_no_field_breakdown(self):
        result = check("shop", [{"title": "a"}])
        self.assertNotIn("fields", result)
