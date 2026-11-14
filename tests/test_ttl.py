import unittest

from tablettl import apply, plan


class TtlTest(unittest.TestCase):
    def test_plan_selects_old(self):
        result = plan("t", [{"name": "p1", "age_days": 40}, {"name": "p2", "age_days": 2}])
        self.assertEqual([p["name"] for p in result["expire"]], ["p1"])

    def test_apply_counts(self):
        self.assertEqual(apply({"expire": [{"name": "p1"}]})["deleted"], 1)

    def test_apply_skips_empty(self):
        self.assertEqual(apply({"expire": []})["skipped"], [])
