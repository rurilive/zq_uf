import unittest

from capticket import resolve, submit


class TicketTest(unittest.TestCase):
    def test_submit_queues(self):
        self.assertGreaterEqual(submit("a", {"url": "https://x"})["queued"], 1)

    def test_resolve_ends(self):
        self.assertFalse(resolve(1, "1234")["resume"])

    def test_priority_recorded_only(self):
        self.assertEqual(submit("b", {"priority": 9})["priority"], 9)
