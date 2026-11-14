import json
import os
import shutil
import tempfile
import unittest

from schemavolve import IncompatibleSchema, SchemaMismatch, read

SCHEMA = ["id", "name", "amount"]

SCHEMA_V2 = {
    "fields": {"id": int, "price": float, "note": str},
    "defaults": {"note": "无备注"},
    "renames": {"amount": "price"},
}


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "data.jsonl")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, rows):
        with open(self.path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    def test_reads_matching_rows(self):
        self.write([{"id": 1, "name": "a", "amount": 3}])
        self.assertEqual(read(self.path, SCHEMA)[0]["name"], "a")

    def test_extra_column_raises(self):
        self.write([{"id": 1, "name": "a", "amount": 3, "note": "x"}])
        with self.assertRaises(SchemaMismatch):
            read(self.path, SCHEMA)

    def test_missing_column_raises(self):
        self.write([{"id": 1, "name": "a"}])
        with self.assertRaises(SchemaMismatch):
            read(self.path, SCHEMA)

    def test_types_not_converted(self):
        self.write([{"id": 1, "name": "a", "amount": "3"}])
        self.assertEqual(read(self.path, SCHEMA)[0]["amount"], "3")


class CompatReaderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "data.jsonl")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, rows):
        with open(self.path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    def test_extra_column_dropped_and_reported(self):
        self.write([{"id": 1, "price": 9.9, "note": "n", "legacy": "x"}])
        result = read(self.path, SCHEMA_V2)
        self.assertNotIn("legacy", result[0])
        self.assertEqual(result.report.dropped_columns, ["legacy"])

    def test_missing_column_filled_with_default(self):
        self.write([{"id": 1, "price": 9.9}])
        result = read(self.path, SCHEMA_V2)
        self.assertEqual(result[0]["note"], "无备注")
        self.assertEqual(result.report.filled_defaults, {"note": "无备注"})

    def test_rename_restored_by_mapping(self):
        self.write([{"id": 1, "amount": 9.9, "note": "n"}])
        result = read(self.path, SCHEMA_V2)
        self.assertEqual(result[0]["price"], 9.9)
        self.assertNotIn("amount", result[0])
        self.assertEqual(result.report.renames_applied, {"amount": "price"})

    def test_rename_conflict_rejected(self):
        self.write([{"id": 1, "amount": 1.0, "price": 2.0, "note": "n"}])
        with self.assertRaises(IncompatibleSchema):
            read(self.path, SCHEMA_V2)

    def test_convertible_type_converted(self):
        self.write([{"id": 1, "price": "9.9", "note": "n"}])
        result = read(self.path, SCHEMA_V2)
        self.assertEqual(result[0]["price"], 9.9)
        self.assertEqual(result.report.conversions, [(1, "price", "str", "float")])

    def test_unconvertible_type_records_line_and_field(self):
        self.write([
            {"id": 1, "price": 1.0, "note": "ok"},
            {"id": 2, "price": "abc", "note": "bad"},
        ])
        result = read(self.path, SCHEMA_V2)
        self.assertEqual(result.report.conversion_errors, [(2, "price", "abc", "float")])
        self.assertEqual(result[1]["price"], "abc")

    def test_missing_column_without_default_rejected(self):
        self.write([{"id": 1, "note": "n"}])
        with self.assertRaises(IncompatibleSchema) as ctx:
            read(self.path, SCHEMA_V2)
        self.assertIn("price", str(ctx.exception))

    def test_non_object_line_rejected(self):
        self.write([["not", "a", "dict"]])
        with self.assertRaises(IncompatibleSchema):
            read(self.path, SCHEMA_V2)

    def test_invalid_spec_raises_clear_error(self):
        self.write([{"id": 1}])
        with self.assertRaises(ValueError):
            read(self.path, {"fields": {"id": "int"}})
        with self.assertRaises(ValueError):
            read(self.path, {"fields": {"id": int}, "defaults": {"ghost": 1}})
