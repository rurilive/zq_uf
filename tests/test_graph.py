import unittest

from linkgraph import build, cycles
from linkgraph import graph


class GraphTest(unittest.TestCase):
    def setUp(self):
        graph._edges.clear()

    def test_build_keeps_direct_edges(self):
        self.assertEqual(build({"a": ["b"], "b": ["c"]})["a"], ["b"])

    def test_cycle_not_detected(self):
        build({"a": ["b"], "b": ["a"]})
        self.assertEqual(cycles(), [])

    def test_duplicate_edges_kept(self):
        self.assertEqual(build({"a": ["b", "b"]})["a"], ["b", "b"])
