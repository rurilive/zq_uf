import unittest

from fieldmerge import merge, reset
from fieldmerge import merge as module


class MergeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_later_source_wins(self):
        row = merge("r1", {"a": {"v": 1}, "b": {"v": 2}})
        self.assertEqual(row["v"], 2)

    def test_cross_source_mixing(self):
        row = merge("r1", {"a": {"x": 1}, "b": {"y": 2}})
        self.assertEqual(set(row), {"x", "y"})

    def test_no_attribution(self):
        self.assertNotIn("source", merge("r1", {"a": {"v": 1}}))
