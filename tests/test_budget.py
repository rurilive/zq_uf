import unittest

from retrybudget import BudgetError, BudgetExhausted, attempt, with_budget


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

    def test_budget_passed_to_child(self):
        root = with_budget({"id": "root"}, 2)
        child = with_budget({"id": "c"}, root)
        attempt(root)
        attempt(child)
        with self.assertRaises(BudgetExhausted):
            attempt(child)

    def test_exhaustion_fails_fast_not_retryable(self):
        task = with_budget({"id": "t"}, 1)
        attempt(task)
        with self.assertRaises(BudgetExhausted) as ctx:
            attempt(task)
        self.assertFalse(ctx.exception.retryable)

    def test_siblings_cannot_overdraft(self):
        root = with_budget({"id": "root"}, 2)
        child_a = with_budget({"id": "a"}, root)
        child_b = with_budget({"id": "b"}, root)
        attempt(child_a)
        attempt(child_b)
        with self.assertRaises(BudgetExhausted):
            attempt(child_a)
        with self.assertRaises(BudgetExhausted):
            attempt(child_b)

    def test_amplification_and_layer_reported(self):
        root = with_budget({"id": "root"}, 2)
        child = with_budget({"id": "c"}, root)
        attempt(root)
        attempt(child)
        with self.assertRaises(BudgetExhausted) as ctx:
            attempt(child)
        self.assertEqual(ctx.exception.amplification, 2)
        self.assertEqual(ctx.exception.layer, 1)

    def test_attempt_without_budget_raises(self):
        with self.assertRaises(BudgetError):
            attempt({"id": "raw"})

    def test_invalid_budget_raises(self):
        with self.assertRaises(BudgetError):
            with_budget({"id": "t"}, -1)
        with self.assertRaises(BudgetError):
            with_budget({"id": "t"}, "3")
