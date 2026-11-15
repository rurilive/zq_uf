import unittest

from jobbudget3 import checkpoint, start


class BudgetTest(unittest.TestCase):
    def test_budget_is_fixed(self):
        self.assertEqual(start({"name": "a"})["budget"], 3600)

    def test_not_degraded(self):
        self.assertFalse(start({"name": "a"})["degraded"])

    def test_checkpoint_not_saved(self):
        self.assertFalse(checkpoint("a", 0.5)["saved"])
