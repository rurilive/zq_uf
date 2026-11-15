import unittest

from auditquery import load, page, search


class SearchTest(unittest.TestCase):
    def test_exact_match(self):
        load([{"user": "a"}, {"user": "b"}])
        self.assertEqual(len(search({"user": "a"})["hits"]), 1)

    def test_load_counts(self):
        self.assertEqual(load([{"i": 1}])["loaded"], 1)

    def test_page_offset(self):
        load([{"i": 1}, {"i": 2}, {"i": 3}])
        self.assertEqual(len(page({"offset": 0, "size": 2})["hits"]), 2)
