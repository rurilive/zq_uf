import unittest

from partretention import apply, plan


class RetentionTest(unittest.TestCase):
    def test_plan_lists_days(self):
        self.assertEqual(plan({"name": "d", "days": ["2026-01-01"]})["expired"], ["2026-01-01"])

    def test_plan_has_no_downstream(self):
        self.assertEqual(plan({"name": "d", "days": []})["downstream"], [])

    def test_apply_deletes_all_at_once(self):
        self.assertEqual(apply({"expired": ["p1", "p2"]})["batches"], 1)
