import json
import os
import shutil
import tempfile
import threading
import unittest

from crawlmanifest import recover, verify, write


def _make_artifact(root, shard, rows=3):
    data_dir = os.path.join(root, "data")
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "shard-%04d.jsonl" % shard)
    with open(path, "w", encoding="utf-8") as handle:
        for i in range(rows):
            handle.write(json.dumps({"shard": shard, "i": i}) + "\n")
    return path


def _write_shard(root, shard, rows=3):
    artifact = _make_artifact(root, shard, rows)
    rel = os.path.relpath(artifact, root)
    return write({"shard": shard, "artifact": rel}, root)


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_write_creates_file(self):
        path = write({"rows": 1}, self.dir)
        self.assertTrue(os.path.exists(path))

    def test_verify_ok(self):
        self.assertTrue(verify(write({"rows": 1}, self.dir))["ok"])

    def test_verify_corrupt(self):
        path = os.path.join(self.dir, "bad.json")
        open(path, "w").write("{")
        self.assertFalse(verify(path)["ok"])


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _leftover_tmp(self):
        mdir = os.path.join(self.dir, "manifests")
        if not os.path.isdir(mdir):
            return []
        return [n for n in os.listdir(mdir) if n.startswith(".tmp-")]

    def test_no_tmp_left_after_write(self):
        write({"shard": 0, "rows": 1}, self.dir)
        self.assertEqual(self._leftover_tmp(), [])

    def test_failed_write_leaves_no_partial(self):
        with self.assertRaises(TypeError):
            write({"shard": 0, "bad": object()}, self.dir)
        mdir = os.path.join(self.dir, "manifests")
        self.assertEqual(os.listdir(mdir), [])

    def test_failed_write_keeps_previous_manifest(self):
        path = write({"shard": 0, "rows": 7}, self.dir)
        with self.assertRaises(TypeError):
            write({"shard": 0, "bad": object()}, self.dir)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["rows"], 7)


class ShardIsolationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_per_shard_files(self):
        _write_shard(self.dir, 0)
        _write_shard(self.dir, 1)
        mdir = os.path.join(self.dir, "manifests")
        self.assertEqual(
            sorted(os.listdir(mdir)),
            ["manifest-shard-0000.json", "manifest-shard-0001.json"],
        )

    def test_concurrent_writes_isolated(self):
        errors = []

        def worker(shard):
            try:
                for _ in range(20):
                    _write_shard(self.dir, shard)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(s,)) for s in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        mdir = os.path.join(self.dir, "manifests")
        self.assertEqual(len(os.listdir(mdir)), 8)
        for name in os.listdir(mdir):
            with open(os.path.join(mdir, name), encoding="utf-8") as handle:
                record = json.load(handle)
            self.assertEqual("manifest-shard-%04d.json" % record["shard"], name)
            self.assertEqual(record["status"], "done")


class VerifyRepairTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _fetch(self, shard):
        artifact = _make_artifact(self.dir, shard)
        return {"artifact": os.path.relpath(artifact, self.dir)}

    def test_checksum_and_rows_recorded(self):
        path = _write_shard(self.dir, 0, rows=5)
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["rows"], 5)
        self.assertTrue(record["checksum"])
        self.assertEqual(record["status"], "done")

    def test_verify_missing_artifact(self):
        path = _write_shard(self.dir, 0)
        os.unlink(os.path.join(self.dir, "data", "shard-0000.jsonl"))
        report = verify(path)
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["missing"]), 1)

    def test_verify_extra_artifact(self):
        _write_shard(self.dir, 0)
        _make_artifact(self.dir, 9)
        report = verify(self.dir)
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["extra"]), 1)

    def test_verify_checksum_mismatch(self):
        _write_shard(self.dir, 0)
        artifact = os.path.join(self.dir, "data", "shard-0000.jsonl")
        with open(artifact, "a", encoding="utf-8") as handle:
            handle.write("{}\n")
        report = verify(self.dir)
        self.assertFalse(report["ok"])
        self.assertEqual(report["corrupt"], [artifact])

    def test_verify_is_readonly(self):
        path = _write_shard(self.dir, 0)
        with open(path, "rb") as handle:
            before = handle.read()
        verify(self.dir)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_recover_repairs_only_diff(self):
        _write_shard(self.dir, 0)
        _write_shard(self.dir, 1)
        good = os.path.join(self.dir, "data", "shard-0000.jsonl")
        with open(good, "rb") as handle:
            before = handle.read()
        # 分片 1 产物损坏，分片 2 清单损坏
        with open(os.path.join(self.dir, "data", "shard-0001.jsonl"), "a") as h:
            h.write("garbage\n")
        bad_manifest = _write_shard(self.dir, 2)
        with open(bad_manifest, "w") as h:
            h.write("{")
        result = recover(self.dir, self._fetch)
        self.assertEqual(result["repaired"], [1, 2])
        self.assertTrue(result["report"]["ok"])
        with open(good, "rb") as handle:
            self.assertEqual(handle.read(), before)  # 好分片未被重写

    def test_recover_verifies_first_and_skips_when_ok(self):
        _write_shard(self.dir, 0)
        calls = []
        result = recover(self.dir, lambda s: calls.append(s))
        self.assertEqual(result["repaired"], [])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
