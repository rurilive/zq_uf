import unittest

from retention import apply, plan


class RetainTest(unittest.TestCase):
    def test_age_filter(self):
        result = plan("orders", [{"name": "p1", "age_days": 40}, {"name": "p2", "age_days": 5}])
        self.assertEqual([p["name"] for p in result["delete"]], ["p1"])

    def test_no_cascade(self):
        result = plan("orders", [{"name": "p1", "age_days": 40}])
        self.assertNotIn("cascade", result)

    def test_no_batch(self):
        self.assertNotIn("batches", apply(plan("orders", [])))
