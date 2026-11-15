import unittest

from sitebudget2 import give_back, take


class BudgetTest(unittest.TestCase):
    def test_take_accumulates_locally(self):
        take("a", 2)
        self.assertEqual(take("a", 3)["used"], 5)

    def test_give_back_reduces(self):
        take("b", 4)
        self.assertEqual(give_back("b", 1)["used"], 3)

    def test_take_never_waits(self):
        self.assertEqual(take("c", 100)["wait"], 0)
