import unittest

from filequeue import ack, get, put, reset


class QueueTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_put_get(self):
        put({"id": 1})
        self.assertEqual(get("c1")["id"], 1)

    def test_same_message_twice(self):
        put({"id": 1})
        self.assertEqual(get("c1")["id"], get("c2")["id"])

    def test_ack_is_noop(self):
        put({"id": 1})
        ack(1)
        self.assertIsNotNone(get("c1"))
