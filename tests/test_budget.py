import unittest

from jobbudget2 import checkpoint, start


class BudgetTest(unittest.TestCase):
    def test_fixed_budget(self):
        self.assertEqual(start("a")["budget"], start("b")["budget"])

    def test_checkpoint_not_saved(self):
        self.assertFalse(checkpoint("a", {"done": 1})["saved"])

    def test_start_keeps_job(self):
        self.assertEqual(start("a")["job"], "a")
