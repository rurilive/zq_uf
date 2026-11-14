import unittest

from depcycle import CycleChecker, GraphError, check, suggest_breaks


class CycleTest(unittest.TestCase):
    def test_linear_graph_ok(self):
        self.assertEqual(check({"a": ["b"], "b": []}), [])

    def test_cycle_detected(self):
        self.assertEqual(check({"a": ["b"], "b": ["a"]}), [["a", "b", "a"]])

    def test_break_suggestion(self):
        suggestions = suggest_breaks({"a": ["b"], "b": ["a"]})
        self.assertEqual(len(suggestions), 1)
        self.assertIn(suggestions[0]["edge"], [("a", "b"), ("b", "a")])


class MultiCycleTest(unittest.TestCase):
    def test_two_independent_cycles(self):
        graph = {"a": ["b"], "b": ["a"], "c": ["d"], "d": ["c"], "e": ["a"]}
        self.assertEqual(check(graph), [["a", "b", "a"], ["c", "d", "c"]])

    def test_self_loop(self):
        self.assertEqual(check({"a": ["a"]}), [["a", "a"]])

    def test_nested_cycles_in_one_scc(self):
        graph = {"a": ["b"], "b": ["c"], "c": ["a", "b"]}
        self.assertEqual(check(graph), [["a", "b", "c", "a"], ["b", "c", "b"]])


class HotUpdateTest(unittest.TestCase):
    def test_incremental_update_detects_new_cycle(self):
        checker = CycleChecker({"a": ["b"], "b": [], "c": ["a"]})
        self.assertEqual(checker.check(), [])
        self.assertEqual(checker.update("b", ["c"]), [["a", "b", "c", "a"]])

    def test_incremental_update_unaffected(self):
        checker = CycleChecker({"a": ["b"], "b": [], "c": []})
        self.assertEqual(checker.update("c", ["a"]), [])
        self.assertEqual(checker.check(), [])

    def test_preview_does_not_mutate(self):
        checker = CycleChecker({"a": ["b"], "b": []})
        self.assertEqual(checker.preview("b", ["a"]), [["a", "b", "a"]])
        self.assertEqual(checker.check(), [])

    def test_update_unknown_node(self):
        checker = CycleChecker({"a": []})
        with self.assertRaises(GraphError):
            checker.update("zzz", ["a"])


class MinBreakTest(unittest.TestCase):
    def test_cheapest_edge_chosen(self):
        graph = {"a": {"b": 1}, "b": {"c": 5}, "c": {"a": 3}}
        suggestions = suggest_breaks(graph)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["edge"], ("a", "b"))
        self.assertEqual(suggestions[0]["weight"], 1.0)

    def test_impact_stats(self):
        graph = {"a": {"b": 1}, "b": {"c": 5}, "c": {"a": 3}, "d": ["a"]}
        suggestions = suggest_breaks(graph)
        self.assertEqual(suggestions[0]["affected"], ["a", "b", "c"])
        self.assertEqual(suggestions[0]["affected_count"], 3)

    def test_multi_edge_break(self):
        graph = {"a": {"b": 1}, "b": {"a": 1, "c": 1}, "c": {"b": 1}}
        suggestions = suggest_breaks(graph)
        edges = {s["edge"] for s in suggestions}
        self.assertEqual(len(edges), 2)
        for s in suggestions:
            self.assertEqual(s["affected_count"], 3)


class ErrorTest(unittest.TestCase):
    def test_unknown_target(self):
        with self.assertRaises(GraphError):
            check({"a": ["ghost"]})

    def test_bad_weight(self):
        with self.assertRaises(GraphError):
            check({"a": {"b": -1}, "b": []})

    def test_bad_format(self):
        with self.assertRaises(GraphError):
            check({"a": "b"})
