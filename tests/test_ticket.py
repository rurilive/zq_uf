import unittest

from formcaptcha import resolve, submit
from formcaptcha import ticket


class TicketTest(unittest.TestCase):
    def setUp(self):
        ticket._queue.clear(); ticket._seq = 0

    def test_submit_returns_id(self):
        self.assertTrue(submit("acc-1", "login")["id"].startswith("T-"))

    def test_queue_order(self):
        submit("a", "t1"); submit("b", "t2")
        self.assertEqual(ticket._queue[0]["account"], "a")

    def test_resolve_ok(self):
        self.assertTrue(resolve("T-1", "8f3k")["ok"])
