import unittest

from proxybudget import consume, remaining
from proxybudget import budget


class BudgetTest(unittest.TestCase):
    def setUp(self):
        budget._spent = 0

    def test_consume_adds(self):
        self.assertEqual(consume("shop", "p1", 10), 10)

    def test_remaining_decreases(self):
        consume("shop", "p1", 5)
        self.assertEqual(remaining("shop"), budget.LIMIT - 5)

    def test_over_limit_raises(self):
        consume("shop", "p1", budget.LIMIT)
        with self.assertRaises(RuntimeError):
            consume("news", "p2", 1)
