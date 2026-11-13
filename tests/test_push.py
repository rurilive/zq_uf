import unittest

from pushdown import push, reset, resume


class PushTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_counts_rows(self):
        self.assertEqual(push([{"id": 1}, {"id": 2}])["pushed"], 2)

    def test_repeat_sends_full_batch(self):
        push([{"id": 1}])
        result = push([{"id": 1}])
        self.assertEqual(result["pushed"], 1)
        self.assertEqual(result["confirmed"], 0)

    def test_resume_without_token(self):
        self.assertEqual(resume("any")["pushed"], 0)
        self.assertIsNone(resume("any")["token"])
