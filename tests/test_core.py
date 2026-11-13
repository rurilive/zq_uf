import unittest

from dupfinder import DupFinder


class DupfinderTest(unittest.TestCase):
    def test_exact_match(self):
        finder = DupFinder()
        finder.index([{"id": "a", "text": "hello"}])
        self.assertEqual(finder.query({"text": "hello"}), ["a"])

    def test_no_match(self):
        finder = DupFinder()
        finder.index([{"id": "a", "text": "hello"}])
        self.assertEqual(finder.query({"text": "hello!"}), [])

    def test_index_count(self):
        finder = DupFinder()
        self.assertEqual(finder.index([{"id": "a", "text": "x"}, {"id": "b", "text": "y"}]), 2)
