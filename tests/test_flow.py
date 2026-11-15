import unittest

from retryflow import outcome, retry


class FlowTest(unittest.TestCase):
    def test_retry_three_times(self):
        for expected in (1, 2, 3):
            result = retry("task-a", "shop")
            self.assertEqual(result["action"], "retry")
            self.assertEqual(result["attempts"], expected)

    def test_give_up_after_three(self):
        for _ in range(3):
            retry("task-b", "shop")
        self.assertEqual(retry("task-b", "shop")["action"], "give_up")

    def test_outcome_counts(self):
        retry("task-c", "shop")
        self.assertEqual(outcome("task-c")["attempts"], 1)
