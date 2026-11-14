import hashlib
import json
import os
import shutil
import tempfile
import unittest

from auditexport import ExportIntegrityError, export, resume


def make_records(count):
    return [{"id": i} for i in range(count)]


class ShardingTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "out.jsonl")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def shard_files(self):
        return sorted(os.listdir(self.path + ".shards"))

    def read_manifest(self):
        with open(self.path + ".manifest.json", encoding="utf-8") as handle:
            return json.load(handle)

    def test_shards_named_by_sequence_and_manifest_records_ranges(self):
        result = export({"records": make_records(5), "shard_size": 2}, self.path)
        self.assertEqual(self.shard_files(),
                         ["000001.jsonl", "000002.jsonl", "000003.jsonl"])
        manifest = self.read_manifest()
        self.assertEqual([s["rows"] for s in manifest["shards"]], [2, 2, 1])
        self.assertEqual(manifest["shards"][0]["range"],
                         {"start_seq": 0, "end_seq": 1})
        self.assertEqual(manifest["shards"][2]["range"],
                         {"start_seq": 4, "end_seq": 4})
        self.assertEqual(result["rows"], 5)
        self.assertEqual(len(result["shards"]), 3)
        self.assertEqual(result["checksum"], manifest["overall"]["sha256"])
        self.assertEqual(result["token"], self.path + ".manifest.json")

    def test_overall_checksum_matches_output_file(self):
        result = export({"records": make_records(4), "shard_size": 2}, self.path)
        with open(self.path, "rb") as handle:
            data = handle.read()
        self.assertEqual(result["checksum"],
                         hashlib.sha256(data).hexdigest())
        self.assertEqual(data.count(b"\n"), 4)

    def test_time_slicing(self):
        records = [{"id": i, "ts": 1000 + i * 10} for i in range(6)]
        result = export({"records": records, "time_key": "ts",
                         "shard_seconds": 30}, self.path)
        manifest = self.read_manifest()
        self.assertEqual([s["rows"] for s in manifest["shards"]], [2, 3, 1])
        self.assertEqual(manifest["shards"][0]["range"],
                         {"start_ts": 1000, "end_ts": 1010})
        self.assertEqual(result["rows"], 6)

    def test_resume_continues_from_last_shard(self):
        spec = {"records": make_records(5), "shard_size": 2}
        with self.assertRaises(RuntimeError) as ctx:
            export(spec, self.path, fail_after_shards=2)
        self.assertEqual(self.shard_files(), ["000001.jsonl", "000002.jsonl"])
        result = resume(ctx.exception.token)
        self.assertEqual(result["rows"], 5)
        self.assertEqual(self.shard_files(),
                         ["000001.jsonl", "000002.jsonl", "000003.jsonl"])
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(len(handle.readlines()), 5)

    def test_resume_only_fills_gaps(self):
        spec = {"records": make_records(5), "shard_size": 2}
        with self.assertRaises(RuntimeError) as ctx:
            export(spec, self.path, fail_after_shards=2)
        shard2 = os.path.join(self.path + ".shards", "000002.jsonl")
        mtime2 = os.stat(shard2).st_mtime_ns
        os.remove(os.path.join(self.path + ".shards", "000001.jsonl"))
        result = resume(ctx.exception.token)
        self.assertEqual(result["rows"], 5)
        # 缺口 000001 被补写，完好的 000002 原样保留
        self.assertTrue(os.path.exists(
            os.path.join(self.path + ".shards", "000001.jsonl")))
        self.assertEqual(os.stat(shard2).st_mtime_ns, mtime2)

    def test_missing_shard_fails_verification(self):
        spec = {"records": make_records(4), "shard_size": 2}
        export(spec, self.path)
        os.remove(os.path.join(self.path + ".shards", "000002.jsonl"))
        with self.assertRaises(ExportIntegrityError) as ctx:
            export(spec, self.path)
        self.assertIn("missing shard 000002.jsonl", str(ctx.exception))

    def test_corrupt_shard_fails_verification(self):
        spec = {"records": make_records(4), "shard_size": 2}
        export(spec, self.path)
        shard1 = os.path.join(self.path + ".shards", "000001.jsonl")
        with open(shard1, "ab") as handle:
            handle.write(b'{"id": 999}\n')
        with self.assertRaises(ExportIntegrityError) as ctx:
            export(spec, self.path)
        self.assertIn("corrupt shard 000001.jsonl", str(ctx.exception))

    def test_idempotent_reexport(self):
        spec = {"records": make_records(5), "shard_size": 2}
        first = export(spec, self.path)
        shards_dir = self.path + ".shards"
        mtimes = {name: os.stat(os.path.join(shards_dir, name)).st_mtime_ns
                  for name in os.listdir(shards_dir)}
        second = export(spec, self.path)
        self.assertEqual(first["checksum"], second["checksum"])
        self.assertEqual(first["rows"], second["rows"])
        for name, mtime in mtimes.items():
            self.assertEqual(
                os.stat(os.path.join(shards_dir, name)).st_mtime_ns, mtime)

    def test_resume_completed_export_is_idempotent(self):
        spec = {"records": make_records(3), "shard_size": 2}
        first = export(spec, self.path)
        second = resume(first["token"])
        self.assertEqual(first["checksum"], second["checksum"])
        self.assertEqual(second["rows"], 3)


if __name__ == "__main__":
    unittest.main()
