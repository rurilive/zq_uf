import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from partcompact import compact, query


class CompactTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for i in range(2):
            with open(os.path.join(self.dir, f"part{i}.jsonl"), "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"id": i}) + "\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_merges_files(self):
        compact(self.dir, [{"id": 1}, {"id": 2}])
        self.assertEqual(len(query(self.dir)), 2)

    def test_old_files_deleted_first(self):
        compact(self.dir, [{"id": 1}])
        self.assertEqual([n for n in os.listdir(self.dir) if n.startswith("part")], [])

    def test_no_manifest(self):
        self.assertNotIn("manifest", {"path": compact(self.dir, [])})

    def test_read_old_during_switch(self):
        started = threading.Event()
        release = threading.Event()

        def rows():
            yield {"id": 10}
            started.set()
            release.wait(5)
            yield {"id": 11}

        worker = threading.Thread(target=lambda: compact(self.dir, rows()))
        worker.start()
        self.assertTrue(started.wait(5))
        self.assertEqual([r["id"] for r in query(self.dir)], [0, 1])
        release.set()
        worker.join(5)
        self.assertEqual([r["id"] for r in query(self.dir)], [10, 11])

    def test_failure_keeps_old_files(self):
        def rows():
            yield {"id": 9}
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            compact(self.dir, rows())
        self.assertEqual([r["id"] for r in query(self.dir)], [0, 1])
        self.assertEqual(sorted(os.listdir(self.dir)), ["part0.jsonl", "part1.jsonl"])

    def test_concurrent_compact(self):
        rows = [{"id": i} for i in range(5)]
        results, errors = [], []

        def run():
            try:
                results.append(compact(self.dir, list(rows)))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(query(self.dir)), 5)
        self.assertEqual(len({r["checksum"] for r in results}), 1)
        self.assertEqual(len([n for n in os.listdir(self.dir) if n.endswith(".jsonl")]), 1)

    def test_idempotent_repeat(self):
        rows = [{"id": 1}, {"id": 2}]
        first = compact(self.dir, rows)
        second = compact(self.dir, rows)
        self.assertTrue(first["switched"])
        self.assertFalse(second["switched"])
        self.assertEqual(first["checksum"], second["checksum"])
        self.assertEqual(len(query(self.dir)), 2)

    def test_switch_failure_rolls_back(self):
        compact(self.dir, [{"id": 1}])
        with open(os.path.join(self.dir, "manifest.json"), encoding="utf-8") as handle:
            old_manifest = json.load(handle)
        real_replace = os.replace

        def boom(src, dst):
            if dst.endswith("manifest.json"):
                raise OSError("switch failed")
            return real_replace(src, dst)

        with patch("os.replace", boom):
            with self.assertRaises(OSError):
                compact(self.dir, [{"id": 2}])
        with open(os.path.join(self.dir, "manifest.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), old_manifest)
        self.assertEqual([r["id"] for r in query(self.dir)], [1])
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".tmp")], [])


if __name__ == "__main__":
    unittest.main()
