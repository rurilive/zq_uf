import unittest

from crawlbudget import LIMIT, consume, remaining, reset


class BudgetTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_consume_counts(self):
        self.assertEqual(consume("shop", 3), 3)
        self.assertEqual(remaining("shop"), LIMIT - 3)

    def test_sites_share_counter(self):
        consume("shop", 10)
        self.assertEqual(remaining("news"), LIMIT - 10)

    def test_over_limit_raises(self):
        consume("shop", LIMIT)
        with self.assertRaises(RuntimeError):
            consume("news", 1)
