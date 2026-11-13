import glob
import hashlib
import json
import os
import tempfile
import unittest

from shardwrite import read, write


def _records(date, count, prefix=""):
    return [
        {"id": prefix + str(index), "date": date, "timestamp": "%sT00:00:%02d" % (date, index % 60)}
        for index in range(count)
    ]


def _manifests(root):
    paths = glob.glob(os.path.join(root, "*", "*.manifest.json"))
    items = []
    for path in sorted(paths):
        with open(path, encoding="utf-8") as handle:
            items.append(json.load(handle))
    return items


class RollingShardTest(unittest.TestCase):
    def test_rollover_by_row_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "store")
            write(_records("2026-03-01", 25), root, max_rows=10, max_bytes=0)

            manifests = _manifests(root)
            self.assertEqual([item["rows"] for item in manifests], [10, 10, 5])
            self.assertEqual([item["seq"] for item in manifests], [1, 2, 3])
            for item in manifests:
                self.assertTrue(os.path.exists(os.path.join(root, "2026-03-01", item["shard"])))

            rows = read(root, date="2026-03-01")
            self.assertEqual(len(rows), 25)
            self.assertEqual([row["id"] for row in rows], [str(i) for i in range(25)])

    def test_rollover_across_multiple_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "store")
            write(_records("2026-03-01", 6), root, max_rows=4, max_bytes=0)
            write(_records("2026-03-02", 3, prefix="b"), root, max_rows=4, max_bytes=0)
            write(_records("2026-03-01", 5, prefix="c"), root, max_rows=4, max_bytes=0)

            day_one = [item for item in _manifests(root) if item["date"] == "2026-03-01"]
            self.assertEqual([item["rows"] for item in day_one], [4, 4, 3])
            self.assertEqual(len(read(root, date="2026-03-01")), 11)
            self.assertEqual(len(read(root, date="2026-03-02")), 3)
            self.assertEqual(len(read(root)), 14)

    def test_rollover_by_byte_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "store")
            records = _records("2026-03-01", 6)
            write(records, root, max_rows=0, max_bytes=120)

            manifests = _manifests(root)
            self.assertGreater(len(manifests), 1)
            for item in manifests[:-1]:
                self.assertLessEqual(item["bytes"], 120)
            self.assertEqual(len(read(root)), 6)


class CrashResidueTest(unittest.TestCase):
    def test_tmp_residue_never_read_and_is_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "store")
            write(_records("2026-03-01", 8), root, max_rows=4, max_bytes=0)

            directory = os.path.join(root, "2026-03-01")
            with open(os.path.join(directory, "2026-03-01-000002.jsonl.tmp"), "w") as handle:
                handle.write('{"id": "half", "date": "2026-03-01"}')
            with open(os.path.join(directory, "2026-03-01-000002.manifest.json.tmp"), "w") as handle:
                handle.write("{")

            rows = read(root, date="2026-03-01")
            self.assertEqual(len(rows), 8)
            self.assertNotIn("half", [row["id"] for row in rows])

            write(_records("2026-03-01", 2, prefix="d"), root, max_rows=4, max_bytes=0)
            self.assertEqual(glob.glob(os.path.join(directory, "*.tmp")), [])
            self.assertEqual(len(read(root)), 10)

    def test_rerun_does_not_mix_old_and_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "store")
            write(_records("2026-03-01", 3), root, max_rows=10, max_bytes=0)
            write(_records("2026-03-01", 3, prefix="r"), root, max_rows=10, max_bytes=0)

            manifests = _manifests(root)
            self.assertEqual(len(manifests), 1)
            self.assertEqual(manifests[0]["rows"], 6)
            self.assertEqual(len(read(root, date="2026-03-01")), 6)


class ManifestChecksumTest(unittest.TestCase):
    def test_manifest_fields_and_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "store")
            write(_records("2026-03-01", 4), root, max_rows=10, max_bytes=0)

            manifest = _manifests(root)[0]
            for field in ("shard", "date", "seq", "rows", "bytes", "checksum", "time_start", "time_end"):
                self.assertIn(field, manifest)
            self.assertEqual(manifest["rows"], 4)
            self.assertEqual(manifest["time_start"], "2026-03-01T00:00:00")
            self.assertEqual(manifest["time_end"], "2026-03-01T00:00:03")

            data_path = os.path.join(root, "2026-03-01", manifest["shard"])
            with open(data_path, "rb") as handle:
                expected = hashlib.sha256(handle.read()).hexdigest()
            self.assertEqual(manifest["checksum"], expected)

    def test_corrupted_shard_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "store")
            write(_records("2026-03-01", 4), root, max_rows=10, max_bytes=0)
            manifest = _manifests(root)[0]
            data_path = os.path.join(root, "2026-03-01", manifest["shard"])
            with open(data_path, "a", encoding="utf-8") as handle:
                handle.write('{"id": "tampered", "date": "2026-03-01"}\n')

            with self.assertRaises(ValueError):
                read(root, date="2026-03-01")
            self.assertEqual(len(read(root, verify=False)), 5)


class RangeReadTest(unittest.TestCase):
    def test_date_range_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "store")
            for day in ("2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04"):
                write(_records(day, 3), root, max_rows=2, max_bytes=0)

            self.assertEqual(len(read(root, start="2026-03-02", end="2026-03-03")), 6)
            self.assertEqual(len(read(root, start="2026-03-03")), 6)
            self.assertEqual(len(read(root, end="2026-03-02")), 6)
            self.assertEqual(len(read(root, start="2026-03-02", end="2026-03-02")), 3)
            self.assertEqual(len(read(root)), 12)

    def test_unknown_date_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "store")
            write([{"id": 1}, {"id": 2, "date": "2026-03-01"}], root)

            self.assertEqual(sorted(row["id"] for row in read(root)), [1, 2])
            self.assertEqual([row["id"] for row in read(root, date="2026-03-01")], [2])
            self.assertEqual([row["id"] for row in read(root, start="2026-03-01")], [2])
            self.assertTrue(os.path.isdir(os.path.join(root, "unknown")))


if __name__ == "__main__":
    unittest.main()
