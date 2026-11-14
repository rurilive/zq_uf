import unittest

from apipage import resume, walk

PAGES = {1: {"items": [1], "next_page": 2}, 2: {"items": [2], "next_page": 3}, 3: {"items": []}}


class PageTest(unittest.TestCase):
    def test_walk_collects(self):
        self.assertEqual(walk(lambda p: PAGES[p])["items"], [1, 2])

    def test_walk_updates_page(self):
        self.assertEqual(walk(lambda p: PAGES[p])["page"], 3)

    def test_resume_page(self):
        self.assertEqual(resume({"page": 2})["page"], 2)
