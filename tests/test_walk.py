import unittest

from paginate import walk

PAGES = {1: ["a", "b"], 2: ["c"]}


class PaginateTest(unittest.TestCase):
    def test_collects_all_pages(self):
        self.assertEqual(walk(lambda page: PAGES.get(page, [])), ["a", "b", "c"])

    def test_stops_on_empty(self):
        calls = []

        def fetch(page):
            calls.append(page)
            return PAGES.get(page, [])

        walk(fetch)
        self.assertEqual(calls, [1, 2, 3])

    def test_empty_first_page(self):
        self.assertEqual(walk(lambda page: []), [])
