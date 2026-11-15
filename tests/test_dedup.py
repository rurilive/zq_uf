import unittest

from rowdedup3 import merge, report


class DedupTest(unittest.TestCase):
    def test_later_overwrites(self):
        merge([{"key": "a", "v": 1}, {"key": "a", "v": 2}])
        self.assertEqual(merge([{"key": "a", "v": 3}])["rows"][0]["v"], 3)

    def test_conflicts_empty(self):
        self.assertEqual(report()["conflicts"], [])

    def test_dropped_zero(self):
        self.assertEqual(report()["dropped"], 0)
