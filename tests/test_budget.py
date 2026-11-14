import unittest

from jobbudget import checkpoint, start


class BudgetTest(unittest.TestCase):
    def test_same_budget(self):
        self.assertEqual(start("big")["budget"], start("small")["budget"])

    def test_no_priority(self):
        self.assertNotIn("priority", start("job"))

    def test_checkpoint_not_saved(self):
        self.assertFalse(checkpoint("job", {"done": 3})["saved"])
