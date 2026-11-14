import unittest

from batchwindow import assign, recompute
from batchwindow import window


class WindowTest(unittest.TestCase):
    def setUp(self):
        window._buckets.clear()

    def test_assign_same_day(self):
        self.assertEqual(assign({"ts": 10})["window"], 0)

    def test_assign_counts(self):
        assign({"ts": 10}); self.assertEqual(assign({"ts": 20})["count"], 2)

    def test_recompute(self):
        assign({"ts": 10})
        self.assertEqual(recompute(0)["count"], 1)
