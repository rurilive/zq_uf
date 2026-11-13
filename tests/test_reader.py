import json
import os
import shutil
import tempfile
import unittest

from schemavolve import SchemaMismatch, read

SCHEMA = ["id", "name", "amount"]


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
