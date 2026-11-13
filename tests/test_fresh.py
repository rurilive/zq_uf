import unittest

from queuefresh import Queue


class FreshTest(unittest.TestCase):
    def test_priority_order(self):
        q = Queue()
        q.push({"id": 1, "priority": 5})
        q.push({"id": 2, "priority": 1})
        self.assertEqual(q.pop()["id"], 2)

    def test_same_priority_fifo(self):
        q = Queue()
        q.push({"id": 1, "priority": 3})
        q.push({"id": 2, "priority": 3})
        self.assertEqual([q.pop()["id"], q.pop()["id"]], [1, 2])

    def test_waiting_does_not_change_score(self):
        q = Queue()
        task = {"id": 1, "priority": 9}
        q.push(task)
        task["priority"] = 1
        self.assertEqual(q.pop()["id"], 1)
        self.assertEqual(q.size(), 0)
