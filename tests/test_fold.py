import unittest

from rowfold import explain, fold


class FoldTest(unittest.TestCase):
    def test_first_row_wins(self):
        result = fold([{"key": "a", "value": 1}, {"key": "a", "value": 2}])
        self.assertEqual(result["rows"][0]["value"], 1)

    def test_folded_count(self):
        self.assertEqual(fold([{"key": "b"}, {"key": "b"}])["folded"], 1)

    def test_reasons_empty(self):
        self.assertEqual(explain("b")["reasons"], [])
