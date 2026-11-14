import unittest

from urlqueue import pop, push
from urlqueue import queue


class QueueTest(unittest.TestCase):
    def setUp(self):
        queue._queue.clear()

    def test_fifo(self):
        push("a"); push("b")
        self.assertEqual(pop()["url"], "a")

    def test_priority_saved(self):
        push("a", 1)
        self.assertEqual(pop()["priority"], 1)

    def test_empty_none(self):
        self.assertIsNone(pop())
