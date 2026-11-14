import unittest
from datetime import datetime

from crontick import next_run, tick


class TickTest(unittest.TestCase):
    def test_next_run_is_later(self):
        now = datetime(2026, 1, 1, 0, 0, 0)
        self.assertGreater(next_run("shop", now), now)

    def test_tick_calls_run(self):
        seen = []
        tick(datetime(2026, 1, 1), lambda task: seen.append(task))
        self.assertEqual(seen, ["shop"])

    def test_tick_reports_ran(self):
        self.assertTrue(tick(datetime(2026, 1, 1), lambda task: None)["ran"])
