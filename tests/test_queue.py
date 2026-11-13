import unittest

from fairqueue import TaskQueue


class QueueTest(unittest.TestCase):
    def test_fifo_order(self):
        q = TaskQueue()
        q.push({"id": 1, "site": "big"})
        q.push({"id": 2, "site": "small"})
        self.assertEqual(q.next()["id"], 1)
        self.assertEqual(q.next()["id"], 2)

    def test_empty_returns_none(self):
        self.assertIsNone(TaskQueue().next())

    def test_retry_goes_to_tail(self):
        q = TaskQueue()
        q.push({"id": 1})
        q.push({"id": 2})
        q.push({"id": 1, "retry": True})
        self.assertEqual([q.next()["id"], q.next()["id"], q.next()["id"]], [1, 2, 1])
