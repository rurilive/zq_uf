import unittest

from multisource import reconcile

A = [{"id": 1, "amount": 10}, {"id": 2, "amount": 20}]
B = [{"id": 1, "amount": 10}]
C = [{"id": 3, "amount": 30}]


class ReconcileTest(unittest.TestCase):
    def test_missing_key_detected(self):
        result = reconcile([A, B], {"key": "id"})
        self.assertEqual([d["key"] for d in result["diffs"]], [2])

    def test_third_source_ignored(self):
        result = reconcile([A, B, C], {"key": "id"})
        self.assertEqual(result["sources"], 2)
        self.assertNotIn(3, [d["key"] for d in result["diffs"]])

    def test_no_attribution(self):
        result = reconcile([A, B], {"key": "id"})
        self.assertEqual(set(result["diffs"][0].keys()), {"key", "kind"})
