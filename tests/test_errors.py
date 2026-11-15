import unittest

from failclass import classify, policy


class ErrorsTest(unittest.TestCase):
    def test_classify_is_generic(self):
        self.assertEqual(classify("timeout")["reason"], "error")

    def test_policy_always_retries(self):
        self.assertEqual(policy("error")["action"], "retry")

    def test_budget_is_task(self):
        self.assertEqual(policy("error")["budget"], "task")
