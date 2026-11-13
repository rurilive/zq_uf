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


def _write(tmp, name, text):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


class ValidatedLoadTest(unittest.TestCase):
    def test_sheet_by_name_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "订单.csv", "单号\nA001\n")
            _write(tmp, "客户.csv", "姓名\n张三\n")
            report = load(tmp, {"sheet": "客户", "columns": [{"name": "姓名"}]})
            self.assertEqual(report.sheet, "客户")
            self.assertEqual(report.rows, [{"姓名": "张三"}])
            report = load(tmp, {"sheet": 0, "columns": [{"name": "姓名"}]})
            self.assertEqual(report.rows[0]["姓名"], "张三")

    def test_column_order_and_extra_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "a.csv", "备注,金额,单号\nx,10,A001\n")
            report = load(path, {"columns": [{"name": "单号"}, {"name": "金额", "type": "int"}]})
        self.assertEqual(report.rows, [{"单号": "A001", "金额": 10}])

    def test_blank_rows_skipped_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "a.csv", "单号\nA001\n\n   \nA002\n")
            report = load(path, {"columns": [{"name": "单号"}]})
        self.assertEqual(report.blank_rows, 2)
        self.assertEqual([row["单号"] for row in report.rows], ["A001", "A002"])

    def test_merged_cells_fill_down(self):
        text = "单号,项目,金额\nA001,甲,10\n,乙,20\n"
        schema = {
            "columns": [
                {"name": "单号", "fill_down": True},
                {"name": "项目"},
                {"name": "金额", "type": "int"},
            ],
            "key": ["单号", "项目"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            report = load(_write(tmp, "a.csv", text), schema)
        self.assertEqual([row["单号"] for row in report.rows], ["A001", "A001"])
        self.assertEqual(report.rows[1]["项目"], "乙")

    def test_formula_value_or_error(self):
        text = "单号,金额\nA001,=10+20\n"
        schema = {"columns": [{"name": "单号"}, {"name": "金额", "type": "int"}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "a.csv", text)
            report = load(path, dict(schema, formulas="value"))
            self.assertEqual(report.rows[0]["金额"], 30)
            report = load(path, dict(schema, formulas="error"))
            self.assertEqual(report.rows, [])
            self.assertEqual(report.errors[0].column, "金额")

    def test_type_errors_record_line_and_column(self):
        text = ("单号,金额,日期\n"
                "A001,abc,2026-03-01\n"
                "A002,10,not-a-date\n"
                "A003,5,2026-03-03\n")
        schema = {"columns": [
            {"name": "单号"},
            {"name": "金额", "type": "int"},
            {"name": "日期", "type": "date"},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            report = load(_write(tmp, "a.csv", text), schema)
        self.assertEqual([row["单号"] for row in report.rows], ["A003"])
        self.assertEqual(report.rows[0]["金额"], 5)
        self.assertEqual(report.rows[0]["日期"].isoformat(), "2026-03-03")
        self.assertEqual([(e.line, e.column) for e in report.errors],
                         [(2, "金额"), (3, "日期")])

    def test_required_enum_range_and_cross_column(self):
        text = ("单号,状态,金额,开始,结束\n"
                ",已采,50,2026-03-01,2026-03-02\n"
                "A002,未知,50,2026-03-01,2026-03-02\n"
                "A003,待采,999,2026-03-01,2026-03-02\n"
                "A004,待采,50,2026-03-05,2026-03-01\n"
                "A005,待采,50,2026-03-01,2026-03-02\n")
        schema = {
            "columns": [
                {"name": "单号", "required": True},
                {"name": "状态", "enum": ["待采", "已采"]},
                {"name": "金额", "type": "int", "range": [0, 100]},
                {"name": "开始", "type": "date"},
                {"name": "结束", "type": "date"},
            ],
            "constraints": [{"expr": "结束 >= 开始", "message": "结束早于开始"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            report = load(_write(tmp, "a.csv", text), schema)
        self.assertEqual([row["单号"] for row in report.rows], ["A005"])
        self.assertEqual(len(report.errors), 4)
        self.assertIn("必填", report.errors[0].reason)

    def test_duplicate_key_skip_and_overwrite(self):
        text = "单号,金额\nA001,10\nA002,20\nA001,99\n"
        schema = {
            "columns": [{"name": "单号"}, {"name": "金额", "type": "int"}],
            "key": ["单号"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "a.csv", text)
            report = load(path, dict(schema, on_conflict="skip"))
            self.assertEqual([row["金额"] for row in report.rows], [10, 20])
            self.assertEqual(len(report.conflicts), 1)
            self.assertEqual(report.conflicts[0]["action"], "skip")
            report = load(path, dict(schema, on_conflict="overwrite"))
            self.assertEqual([row["金额"] for row in report.rows], [99, 20])

    def test_dry_run_and_batches(self):
        text = "单号\n" + "".join("A%03d\n" % i for i in range(5))
        schema = {"columns": [{"name": "单号"}], "batch_size": 2}
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "a.csv", text)
            dry = load(path, dict(schema, dry_run=True))
            self.assertTrue(dry.dry_run)
            self.assertEqual(len(dry.rows), 5)
            self.assertEqual(dry.batches, [])
            self.assertEqual(dry.committed, 0)
            done = load(path, schema)
            self.assertEqual([len(b) for b in done.batches], [2, 2, 1])
            self.assertEqual(done.committed, 5)
