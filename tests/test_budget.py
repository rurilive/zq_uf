import unittest

from retrybudget import BudgetError, attempt, budget_report, with_budget


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


class SharedBudgetTest(unittest.TestCase):
    def test_budget_passed_to_child(self):
        parent = with_budget({"id": "p", "layer": "L1"}, 2)
        attempt(parent)
        child = with_budget({"id": "c", "layer": "L2"}, parent)
        self.assertEqual(child["budget"], 1)
        self.assertEqual(attempt(child)["attempt"], 1)
        self.assertTrue(attempt(parent)["exhausted"])

    def test_exhausted_fails_fast(self):
        root = with_budget({"id": "r", "layer": "L1"}, 1)
        attempt(root)
        child = with_budget({"id": "c", "layer": "L2"}, root)
        result = attempt(child)
        self.assertTrue(result["exhausted"])
        self.assertFalse(result["retry"])
        self.assertEqual(result["abandoned_at"], "L2")
        self.assertNotIn("attempts", child)

    def test_siblings_cannot_overdraw(self):
        root = with_budget({"id": "r"}, 2)
        sib_a = with_budget({"id": "a"}, root)
        sib_b = with_budget({"id": "b"}, root)
        results = [attempt(sib_a), attempt(sib_b), attempt(sib_a), attempt(sib_b)]
        granted = [r for r in results if not r.get("exhausted")]
        self.assertEqual(len(granted), 2)
        self.assertEqual(budget_report(root)["attempts"], 2)

    def test_amplification_report(self):
        root = with_budget({"id": "r", "layer": "L1"}, 2)
        child = with_budget({"id": "c", "layer": "L2"}, root)
        attempt(root)
        attempt(child)
        attempt(child)
        stats = budget_report(root)
        self.assertEqual(stats["amplification"], 2.0)
        self.assertEqual(stats["abandoned_at"], "L2")
        self.assertEqual(stats["budget_remaining"], 0)

    def test_invalid_usage_raises(self):
        with self.assertRaises(BudgetError):
            attempt({"id": "x"})
        with self.assertRaises(BudgetError):
            with_budget({"id": "x"}, 0)
        with self.assertRaises(BudgetError):
            with_budget({"id": "x"}, {"id": "no-budget"})
