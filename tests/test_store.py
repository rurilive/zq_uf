import os
import tempfile
import unittest

from shardwrite import read, write


class ShardwriteTest(unittest.TestCase):
    def test_write_returns_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rows.jsonl")
            self.assertEqual(write([{"id": 1}, {"id": 2}], path), 2)

    def test_read_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rows.jsonl")
            write([{"id": 1, "date": "2026-03-01"}], path)
            write([{"id": 2, "date": "2026-03-02"}], path)
            self.assertEqual([row["id"] for row in read(path)], [1, 2])

    def test_filter_by_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rows.jsonl")
            write([{"id": 1, "date": "2026-03-01"}, {"id": 2, "date": "2026-03-02"}], path)
            self.assertEqual([row["id"] for row in read(path, date="2026-03-01")], [1])
