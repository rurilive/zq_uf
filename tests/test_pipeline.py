import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from sheetload import load


def write(tmp, name, text):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


SCHEMA = {
    "fields": [
        {"name": "单号", "required": True},
        {"name": "金额", "type": "decimal", "min": Decimal("0")},
        {"name": "日期", "type": "date"},
        {"name": "状态", "enum": ["待处理", "已处理"]},
        {"name": "备注", "merged": True},
    ],
    "key": "单号",
}

HEADER = "单号,金额,日期,状态,备注\n"


class PipelineTest(unittest.TestCase):
    def test_sheet_by_name_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "alpha.csv", HEADER + "A001,1.00,2026-01-01,待处理,x\n")
            write(tmp, "beta.csv", HEADER + "B001,2.00,2026-01-02,已处理,y\n")
            by_name = load(tmp, dict(SCHEMA, sheet="beta"))
            by_index = load(tmp, dict(SCHEMA, sheet=1))
        self.assertEqual(by_name["sheet"], "beta")
        self.assertEqual(by_name["rows"][0]["单号"], "B001")
        self.assertEqual(by_index["rows"][0]["单号"], "B001")

    def test_sheet_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "alpha.csv", HEADER)
            with self.assertRaises(ValueError):
                load(tmp, dict(SCHEMA, sheet="nope"))

    def test_column_order_and_extra_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "a.csv",
                         "备注,金额,额外,单号,日期,状态\n"
                         "x,1.50,忽略,A001,2026-01-01,待处理\n")
            report = load(path, SCHEMA)
        self.assertEqual(report["total"], 1)
        row = report["rows"][0]
        self.assertEqual(row["单号"], "A001")
        self.assertEqual(row["金额"], Decimal("1.50"))
        self.assertEqual(row["日期"], date(2026, 1, 1))
        self.assertNotIn("额外", row)

    def test_empty_rows_skipped_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "a.csv",
                         HEADER
                         + "A001,1.00,2026-01-01,待处理,x\n"
                         + "\n"
                         + " , , , , \n"
                         + "A002,2.00,2026-01-02,已处理,y\n")
            report = load(path, SCHEMA)
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["skipped_empty"], 2)

    def test_merged_cells_fill_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "a.csv",
                         HEADER
                         + "A001,1.00,2026-01-01,待处理,首批\n"
                         + "A002,2.00,2026-01-02,待处理,\n")
            report = load(path, SCHEMA)
        self.assertEqual(report["rows"][1]["备注"], "首批")

    def test_formula_reported_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "a.csv", HEADER + 'A001,1.00,2026-01-01,待处理,"=SUM(1,2)"\n')
            report = load(path, SCHEMA)
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["errors"][0]["row"], 2)
        self.assertEqual(report["errors"][0]["column"], "备注")

    def test_formula_keep_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "a.csv", HEADER + 'A001,1.00,2026-01-01,待处理,"=SUM(1,2)"\n')
            report = load(path, dict(SCHEMA, formula="keep"))
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["rows"][0]["备注"], "=SUM(1,2)")

    def test_type_error_records_row_and_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "a.csv",
                         HEADER
                         + "A001,不是数字,2026-01-01,待处理,x\n"
                         + "A002,2.00,2026-13-40,待处理,y\n"
                         + "A003,3.00,2026-01-03,已处理,z\n")
            report = load(path, SCHEMA)
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["rows"][0]["单号"], "A003")
        positions = {(e["row"], e["column"]) for e in report["errors"]}
        self.assertIn((2, "金额"), positions)
        self.assertIn((3, "日期"), positions)

    def test_required_enum_and_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "a.csv",
                         HEADER
                         + ",1.00,2026-01-01,待处理,x\n"
                         + "A002,2.00,2026-01-02,未知,y\n"
                         + "A003,-1.00,2026-01-03,待处理,z\n")
            report = load(path, SCHEMA)
        self.assertEqual(report["total"], 0)
        columns = [e["column"] for e in report["errors"]]
        self.assertEqual(columns, ["单号", "状态", "金额"])

    def test_optional_empty_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "a.csv", HEADER + "A001,1.00,2026-01-01,待处理,\n")
            report = load(path, SCHEMA)
        self.assertIsNone(report["rows"][0]["备注"])

    def test_cross_column_constraint(self):
        schema = {
            "fields": [
                {"name": "单号"},
                {"name": "开始", "type": "date"},
                {"name": "结束", "type": "date"},
            ],
            "constraints": [{"left": "开始", "op": "<=", "right": "结束"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "a.csv",
                         "单号,开始,结束\n"
                         "A001,2026-01-01,2026-01-02\n"
                         "A002,2026-01-05,2026-01-02\n")
            report = load(path, schema)
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["errors"][0]["row"], 3)
        self.assertEqual(report["errors"][0]["column"], "开始")

    def test_duplicate_key_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "a.csv",
                         HEADER
                         + "A001,1.00,2026-01-01,待处理,x\n"
                         + "A001,9.99,2026-01-02,已处理,y\n")
            report = load(path, dict(SCHEMA, on_conflict="skip"))
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["rows"][0]["金额"], Decimal("1.00"))
        self.assertEqual(len(report["conflicts"]), 1)
        self.assertEqual(report["conflicts"][0]["key"], ("A001",))

    def test_duplicate_key_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "a.csv",
                         HEADER
                         + "A001,1.00,2026-01-01,待处理,x\n"
                         + "A001,9.99,2026-01-02,已处理,y\n")
            report = load(path, dict(SCHEMA, on_conflict="overwrite"))
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["rows"][0]["金额"], Decimal("9.99"))
        self.assertEqual(len(report["conflicts"]), 1)

    def test_duplicate_key_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "a.csv",
                         HEADER
                         + "A001,1.00,2026-01-01,待处理,x\n"
                         + "A001,9.99,2026-01-02,已处理,y\n")
            report = load(path, dict(SCHEMA, on_conflict="error"))
        self.assertEqual(report["total"], 1)
        self.assertEqual(len(report["conflicts"]), 1)
        self.assertEqual(len(report["errors"]), 1)

    def test_dry_run_only_reports(self):
        committed = []
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "a.csv",
                         HEADER
                         + "A001,1.00,2026-01-01,待处理,x\n"
                         + "A002,坏,2026-01-02,已处理,y\n")
            report = load(path, dict(SCHEMA, dry_run=True, commit=committed.extend))
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["batches"], [])
        self.assertEqual(committed, [])
        self.assertEqual(len(report["errors"]), 1)

    def test_batches_committed(self):
        committed = []
        body = "".join(
            "A%03d,1.00,2026-01-0%d,待处理,x\n" % (i, i) for i in range(1, 4))
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "a.csv", HEADER + body)
            report = load(path, dict(SCHEMA, batch_size=2, commit=committed.extend))
        self.assertEqual([len(b) for b in report["batches"]], [2, 1])
        self.assertEqual([row["单号"] for row in committed],
                         ["A001", "A002", "A003"])


if __name__ == "__main__":
    unittest.main()

