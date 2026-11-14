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

    def test_self_loop(self):
        build({"a": ["a"]})
        report = cycles(limit=10)
        self.assertEqual(report["cycles"], [{"path": ["a"], "nodes": ["a"]}])
        self.assertFalse(report["truncated"])

    def test_two_cycle(self):
        build({"a": ["b"], "b": ["a"]})
        report = cycles(limit=10)
        self.assertEqual(report["cycles"], [{"path": ["a", "b"], "nodes": ["a", "b"]}])

    def test_strongly_connected(self):
        build({"a": ["b"], "b": ["c"], "c": ["a", "b"]})
        report = cycles(limit=10)
        self.assertEqual(len(report["cycles"]), 1)
        self.assertEqual(report["cycles"][0]["path"], ["a", "b", "c"])
        self.assertEqual(report["cycles"][0]["nodes"], ["a", "b", "c"])

    def test_param_variants_merged(self):
        build({
            "http://x.com/a?p=1": ["http://x.com/b#top"],
            "http://x.com/b": ["HTTP://x.com/a?p=2"],
        })
        report = cycles(limit=10)
        self.assertEqual(report["nodes"], 2)
        self.assertEqual(report["edges"], 2)
        self.assertEqual(
            report["cycles"],
            [{"path": ["http://x.com/a", "http://x.com/b"],
              "nodes": ["http://x.com/a", "http://x.com/b"]}],
        )

    def test_duplicate_cycle_reported_once(self):
        build({"a": ["b", "b"], "b": ["a", "a"]})
        report = cycles(limit=10)
        self.assertEqual(len(report["cycles"]), 1)
        self.assertEqual(report["edges"], 2)

    def test_truncation(self):
        pages = {f"n{i}": [f"n{(i + 1) % 50}"] for i in range(50)}
        build(pages)
        small = cycles(limit=10)
        self.assertTrue(small["truncated"])
        self.assertEqual(small["nodes"], 50)
        full = cycles(limit=50)
        self.assertFalse(full["truncated"])
        self.assertEqual(len(full["cycles"]), 1)

    def test_invalid_input(self):
        with self.assertRaises(TypeError):
            build(["not", "a", "mapping"])
        with self.assertRaises(TypeError):
            build({"a": [1]})
        with self.assertRaises(TypeError):
            build({"a": "b"})
        with self.assertRaises(ValueError):
            build({"a": ["  "]})
        with self.assertRaises(ValueError):
            cycles(limit=0)
        with self.assertRaises(TypeError):
            cycles(limit="10")
