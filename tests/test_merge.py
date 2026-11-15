import unittest

from crawlmerge2 import merge, report


class MergeTest(unittest.TestCase):
    def test_later_row_wins(self):
        first = {"rows": [{"key": "a", "value": 1}]}
        second = {"rows": [{"key": "a", "value": 2}]}
        report_data = merge([first, second])
        self.assertEqual(report_data["merged"], 1)
        self.assertEqual(report()["rows"], 1)

    def test_conflicts_not_reported(self):
        merge([{"rows": [{"key": "b", "v": 1}]}, {"rows": [{"key": "b", "v": 2}]}])
        self.assertEqual(report()["conflicts"], [])

    def test_empty_batches(self):
        self.assertEqual(merge([])["merged"], 0)
