import unittest

from formbatch import resume, submit


class BatchTest(unittest.TestCase):
    def test_submit_all_forms(self):
        result = submit([{"name": "a"}, {"name": "b"}])
        self.assertEqual(len(result["results"]), 2)

    def test_submit_marks_every_row_ok(self):
        result = submit([{"name": "a"}])
        self.assertTrue(result["results"][0]["ok"])

    def test_resume_has_no_state(self):
        self.assertFalse(resume("last")["resumed"])
