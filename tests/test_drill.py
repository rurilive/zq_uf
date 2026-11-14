import importlib
import unittest

from restoredrill import drill, report

module = importlib.import_module("restoredrill.drill")


class DrillTest(unittest.TestCase):
    def setUp(self):
        module._history.clear()

    def test_drill_ok(self):
        self.assertTrue(drill("snap-1")["ok"])

    def test_snapshot_echoed(self):
        self.assertEqual(drill("snap-1")["snapshot"], "snap-1")

    def test_report_counts(self):
        drill("snap-1")
        self.assertEqual(report()["drills"], 1)
