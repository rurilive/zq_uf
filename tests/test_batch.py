import unittest

from changebatch import apply, resume


class BatchTest(unittest.TestCase):
    def test_apply_counts(self):
        self.assertEqual(apply({"id": "b1", "rows": [{}, {}]})["applied"], 2)

    def test_failure_keeps_written(self):
        self.assertEqual(apply({"id": "b2", "rows": [{}, {"bad": True}]})["applied"], 1)

    def test_resume_no_skip(self):
        apply({"id": "b3", "rows": [{}]})
        self.assertEqual(resume("b3")["skipped"], 0)
