import unittest
from datetime import datetime

from crawlclock import now, sync


class ClockTest(unittest.TestCase):
    def test_now_is_datetime(self):
        self.assertIsInstance(now(), datetime)

    def test_sync_reports_no_drift(self):
        self.assertEqual(sync(datetime.now())["drift"], 0.0)

    def test_sync_does_not_correct(self):
        self.assertFalse(sync(datetime.now())["corrected"])
