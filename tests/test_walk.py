import unittest

from pagecycle import walk


class WalkTest(unittest.TestCase):
    def test_linear_pages(self):
        pages = {
            "p1": {"url": "p1", "next": "p2"},
            "p2": {"url": "p2", "next": None},
        }
        self.assertEqual(walk(lambda u: pages[u], start="p1"), ["p1", "p2"])

    def test_cycle_repeats_until_limit(self):
        pages = {
            "p1": {"url": "p1", "next": "p2"},
            "p2": {"url": "p2", "next": "p1"},
        }
        seen = walk(lambda u: pages[u], start="p1", limit=6)
        self.assertEqual(seen, ["p1", "p2"] * 3)

    def test_no_visited_state(self):
        pages = {"p1": {"url": "p1", "next": None}}
        self.assertIsInstance(walk(lambda u: pages[u], start="p1"), list)
