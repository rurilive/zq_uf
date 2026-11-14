import unittest

from rowmerge import merge, report


class MergeTest(unittest.TestCase):
    def test_later_wins(self):
        result = merge([{"id": 1, "v": 2}, {"id": 1, "v": 1}])
        self.assertEqual(result["rows"][1]["v"], 1)

    def test_no_conflicts(self):
        self.assertEqual(merge([{"id": 1}])["conflicts"], [])

    def test_report_empty(self):
        self.assertEqual(report(), [])
