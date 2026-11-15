import unittest

from cdcbatch import apply, resume


class BatchTest(unittest.TestCase):
    def test_apply_counts_rows(self):
        self.assertEqual(apply({"id": "b1", "rows": [{}, {}]})["applied"], 2)

    def test_failure_does_not_roll_back(self):
        result = apply({"id": "b2", "rows": [{}, {"bad": True}]})
        self.assertEqual(result["applied"], 1)
        self.assertFalse(result["rolled_back"])

    def test_resume_replays(self):
        apply({"id": "b3", "rows": [{}]})
        self.assertEqual(resume("b3")["skipped"], 0)
