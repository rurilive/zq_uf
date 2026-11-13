import unittest

from pagedigest import fetch_all, parse_page

PAGES = {
    1: {"items": [{"id": 1, "title": "alpha", "price": "10.00", "url": "http://shop/1"}]},
    2: {"items": [{"id": 2, "title": "beta", "price": "20.00", "url": "http://shop/2"}]},
}


def fake_fetch(page):
    return PAGES.get(page, {"items": []})


class PagedigestTest(unittest.TestCase):
    def test_parse_fields(self):
        items = parse_page({"items": [{"id": 7, "title": "x", "price": "1.50", "url": "http://shop/7"}]})
        self.assertEqual(items[0].item_id, "7")
        self.assertEqual(items[0].price, "1.50")

    def test_fetch_two_pages(self):
        items = fetch_all(fake_fetch)
        self.assertEqual([item.title for item in items], ["alpha", "beta"])

    def test_empty_page_stops(self):
        calls = []

        def counting(page):
            calls.append(page)
            return PAGES.get(page, {"items": []})

        fetch_all(counting)
        self.assertEqual(calls, [1, 2, 3])
