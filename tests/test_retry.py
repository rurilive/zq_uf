import unittest

from retrywindow import due, schedule
from retrywindow import retry


class RetryTest(unittest.TestCase):
    def setUp(self):
        retry._tasks.clear()

    def test_first_attempt_retries(self):
        self.assertTrue(schedule("t1", "shop")["retry"])

    def test_three_attempts_stop(self):
        for _ in range(3):
            result = schedule("t1", "shop")
        self.assertFalse(result["retry"])

    def test_due_lists_pending(self):
        schedule("t1", "shop")
        self.assertIn("t1", due())
