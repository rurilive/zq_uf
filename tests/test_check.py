import importlib
import unittest

from linkcheck import check, report

module = importlib.import_module("linkcheck.check")


class CheckTest(unittest.TestCase):
    def setUp(self):
        module._results.clear()

    def test_checks_all(self):
        self.assertEqual(check(["a", "b"])["checked"], 2)

    def test_workers_ignored(self):
        self.assertEqual(check(["a"], 8)["workers"], 1)

    def test_report_rows(self):
        check(["a"])
        self.assertEqual(report()[0]["url"], "a")
