import unittest

from auditsearch import load, page, search


class SearchTest(unittest.TestCase):
    def test_search_exact_match(self):
        load([{"user": "a", "op": "select"}, {"user": "b", "op": "drop"}])
        self.assertEqual(len(search({"user": "a"})["hits"]), 1)

    def test_search_returns_everything(self):
        load([{"user": "a"}, {"user": "b"}])
        self.assertEqual(len(search({})["hits"]), 2)

    def test_page_uses_offset(self):
        load([{"i": 1}, {"i": 2}, {"i": 3}])
        self.assertEqual(len(page({"offset": 0, "size": 2})["hits"]), 2)
