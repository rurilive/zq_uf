import os
import tempfile
import unittest

from sheetload import load

SHEET = "单号,金额,日期\nA001,10.00,2026-03-01\nA002,20.00,2026-03-02\n"


class SheetloadTest(unittest.TestCase):
    def test_rows_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            open(path, "w", encoding="utf-8").write(SHEET)
            rows = load(path, ["单号", "金额"])
        self.assertEqual([row["单号"] for row in rows], ["A001", "A002"])

    def test_missing_column_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            open(path, "w", encoding="utf-8").write(SHEET)
            rows = load(path, ["单号", "备注"])
        self.assertEqual(rows[0]["备注"], "")
