import unittest

from depcycle import check, suggest_breaks


class CycleTest(unittest.TestCase):
    def test_linear_graph_ok(self):
        self.assertEqual(check({"a": ["b"], "b": []}), [])

    def test_cycle_not_detected(self):
        self.assertEqual(check({"a": ["b"], "b": ["a"]}), [])

    def test_no_break_suggestion(self):
        self.assertEqual(suggest_breaks({"a": ["b"], "b": ["a"]}), [])
