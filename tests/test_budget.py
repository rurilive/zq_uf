import unittest

from retrybudget import attempt, with_budget


class BudgetTest(unittest.TestCase):
    def test_fixed_three_attempts(self):
        task = with_budget({"id": "t"}, 100)
        seq = [attempt(task)["retry"] for _ in range(3)]
        self.assertEqual(seq, [True, True, False])

    def test_budget_not_consumed(self):
        task = with_budget({"id": "t"}, 1)
        attempt(task)
        self.assertEqual(task["budget"], 1)

    def test_no_amplification_report(self):
        task = with_budget({"id": "t"}, 1)
        self.assertNotIn("amplification", attempt(task))
