import unittest

from schemagray import apply, plan


class GrayTest(unittest.TestCase):
    def test_plan_single_batch(self):
        self.assertEqual(len(plan({"tables": ["a", "b"]})["batches"]), 1)

    def test_apply_all(self):
        self.assertEqual(apply({"tables": ["a"]})["applied"], ["a"])

    def test_never_paused(self):
        self.assertFalse(apply({"tables": []})["paused"])
