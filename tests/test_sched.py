import unittest

from jobpriority import next_job, submit
from jobpriority import sched


class SchedTest(unittest.TestCase):
    def setUp(self):
        sched._queue.clear()

    def test_fifo(self):
        submit({"id": 1}); submit({"id": 2})
        self.assertEqual(next_job()["id"], 1)

    def test_empty_none(self):
        self.assertIsNone(next_job())

    def test_submit_count(self):
        self.assertEqual(submit({"id": 1}), 1)
