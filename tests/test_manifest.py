import json
import os
import tempfile
import unittest

from manifestcheck import (export_diff, export_manifest, export_repairs,
                           repair, repair_log, rollback, verify, write)


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = os.path.join(self.tmp.name, "data")
        self.source = os.path.join(self.tmp.name, "source")
        for folder in (self.data, self.source):
            os.makedirs(folder)
            for name, content in (("part-0-a", b"aaa"), ("part-1-b", b"bbb")):
                with open(os.path.join(folder, name), "wb") as handle:
                    handle.write(content)
        self.path = os.path.join(self.tmp.name, "manifest.json")
        self.manifest = {
            "path": self.path,
            "data_dir": self.data,
            "files": [{"name": "part-0-a", "shard": 0},
                      {"name": "part-1-b", "shard": 1}],
        }

    def test_write_records_size_checksum_shard(self):
        result = write(self.manifest)
        self.assertTrue(result["ok"])
        self.assertIsNotNone(result["checksum"])
        doc = load_json(self.path)
        entry = doc["files"][0]
        self.assertEqual(entry["size"], 3)
        self.assertEqual(len(entry["checksum"]), 64)
        self.assertEqual(entry["shard"], 0)

    def test_atomic_write_and_version_monotonic(self):
        first = write(self.manifest)
        second = write(self.manifest)
        self.assertEqual((first["version"], second["version"]), (1, 2))
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        self.assertTrue(os.path.exists(self.path + ".bak"))

    def test_failed_write_keeps_original(self):
        write(self.manifest)
        bad = dict(self.manifest, files=[{"name": "ghost", "shard": 0}])
        result = write(bad)
        self.assertFalse(result["ok"])
        self.assertEqual(result["version"], 1)
        doc = load_json(self.path)
        self.assertEqual(doc["version"], 1)
        self.assertEqual(len(doc["files"]), 2)

    def test_rollback_restores_previous_version(self):
        write(self.manifest)
        write(self.manifest)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        result = rollback(self.path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], 1)

    def test_verify_missing_extra_corrupt(self):
        write(self.manifest)
        os.remove(os.path.join(self.data, "part-0-a"))
        with open(os.path.join(self.data, "part-1-b"), "ab") as handle:
            handle.write(b"x")
        open(os.path.join(self.data, "stray"), "w").close()
        result = verify(self.path, data_dir=self.data)
        self.assertFalse(result["ok"])
        self.assertEqual(result["missing"], ["part-0-a"])
        self.assertEqual(result["corrupt"], ["part-1-b"])
        self.assertEqual(result["extra"], ["stray"])

    def test_verify_single_shard_trial(self):
        write(self.manifest)
        os.remove(os.path.join(self.data, "part-0-a"))
        result = verify(self.path, data_dir=self.data, shard=1)
        self.assertTrue(result["ok"])
        result = verify(self.path, data_dir=self.data, shard=0)
        self.assertEqual(result["missing"], ["part-0-a"])

    def test_repair_only_fills_missing(self):
        write(self.manifest)
        os.remove(os.path.join(self.data, "part-0-a"))
        open(os.path.join(self.data, "stray"), "w").close()
        result = repair(self.path, source_dir=self.source, data_dir=self.data)
        self.assertEqual(result["repaired"], ["part-0-a"])
        self.assertEqual(result["version"], 1)
        self.assertTrue(os.path.exists(os.path.join(self.data, "stray")))
        self.assertEqual(verify(self.path, data_dir=self.data)["missing"], [])

    def test_repair_log_by_day(self):
        write(self.manifest)
        os.remove(os.path.join(self.data, "part-0-a"))
        repair(self.path, source_dir=self.source, data_dir=self.data)
        logs = repair_log(self.path)
        self.assertEqual(len(logs), 1)
        day, records = next(iter(logs.items()))
        self.assertEqual(records[0]["repaired"], ["part-0-a"])
        self.assertEqual(repair_log(self.path, day=day), logs)

    def test_exports(self):
        write(self.manifest)
        os.remove(os.path.join(self.data, "part-0-a"))
        diff_out = os.path.join(self.tmp.name, "diff.json")
        exported = export_diff(self.path, diff_out, data_dir=self.data)
        self.assertEqual(exported["missing"], ["part-0-a"])
        self.assertEqual(load_json(diff_out)["missing"], ["part-0-a"])
        shard_out = os.path.join(self.tmp.name, "shard-0.json")
        result = export_manifest(self.path, shard_out, shard=0)
        self.assertEqual(result["files"], 1)
        doc = load_json(shard_out)
        self.assertEqual([e["name"] for e in doc["files"]], ["part-0-a"])
        repair(self.path, source_dir=self.source, data_dir=self.data)
        repairs_out = os.path.join(self.tmp.name, "repairs.json")
        result = export_repairs(self.path, repairs_out)
        self.assertEqual(len(result["days"]), 1)
        self.assertTrue(os.path.exists(repairs_out))


if __name__ == "__main__":
    unittest.main()
