import unittest

from crawlbackoff import budget, signal
from crawlbackoff import backoff


class BackoffTest(unittest.TestCase):
    def setUp(self):
        backoff._state.clear()

    def test_signal_records_status(self):
        self.assertEqual(signal("shop", 429)["status"], 429)

    def test_budget_after_signal(self):
        signal("shop", 429)
        self.assertEqual(budget("shop")["remaining"], 3)

    def test_budget_default(self):
        self.assertEqual(budget("news")["status"], "ok")
