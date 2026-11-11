import unittest

from crawlflow import Scheduler


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.scheduler = Scheduler()

    def test_exact_dedup(self):
        self.assertTrue(self.scheduler.add("https://example.com/a"))
        self.assertFalse(self.scheduler.add("https://example.com/a"))
        self.assertEqual(self.scheduler.pending(), 1)

    def test_success(self):
        self.scheduler.add("https://example.com/a")
        task = self.scheduler.next()
        self.scheduler.success(task.url, "body")
        self.assertIsNone(self.scheduler.next())

    def test_retry_then_drop(self):
        self.scheduler.add("https://example.com/a")
        for _ in range(3):
            task = self.scheduler.next()
            self.assertIsNotNone(task)
            self.scheduler.failure(task.url)
        self.assertIsNone(self.scheduler.next())
