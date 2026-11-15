import unittest

from partlife import apply, plan


class LifeTest(unittest.TestCase):
    def test_plan_lists_all(self):
        self.assertEqual(plan({"name": "d", "days": ["p1", "p2"]})["expired"], ["p1", "p2"])

    def test_no_tiers(self):
        self.assertEqual(plan({"name": "d", "days": []})["tiers"], {})

    def test_apply_single_batch(self):
        self.assertEqual(apply({"expired": ["p1"]})["batches"], 1)
