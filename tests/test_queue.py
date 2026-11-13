import unittest

from taskqueue import TaskQueue


class TaskQueueTest(unittest.TestCase):
    def test_fifo_order(self):
        queue = TaskQueue()
        queue.push("a")
        queue.push("b")
        self.assertEqual(queue.lease("w1"), "a")
        self.assertEqual(queue.lease("w1"), "b")

    def test_empty_returns_none(self):
        self.assertIsNone(TaskQueue().lease("w1"))

    def test_complete_records_task(self):
        queue = TaskQueue()
        queue.push("a")
        task = queue.lease("w1")
        queue.complete(task)
        self.assertEqual(queue.done, ["a"])
