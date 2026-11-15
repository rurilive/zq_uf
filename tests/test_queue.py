import unittest

from crawlprio2 import pop, push


class QueueTest(unittest.TestCase):
    def setUp(self):
        while pop() is not None:
            pass

    def test_fifo(self):
        push({"id": "a"})
        push({"id": "b"})
        self.assertEqual(pop()["id"], "a")

    def test_empty_returns_none(self):
        while pop() is not None:
            pass
        self.assertIsNone(pop())

    def test_priority_ignored(self):
        push({"id": "low", "priority": 1})
        push({"id": "high", "priority": 9})
        self.assertEqual(pop()["id"], "low")
