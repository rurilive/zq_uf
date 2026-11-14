import os
import json
import shutil
import tempfile
import threading
import unittest

from pagestore import delete, gc, get, put


def _block_files(root):
    blocks_dir = os.path.join(root, "blocks")
    return [name for name in os.listdir(blocks_dir)
            if len(name) == 64 and not name.endswith(".json")]


def _refs(root):
    with open(os.path.join(root, "blocks", "refs.json"), encoding="utf-8") as handle:
        return json.load(handle)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_put_get(self):
        put("p1", "hello", root=self.dir)
        self.assertEqual(get("p1", root=self.dir), "hello")

    def test_duplicate_content_two_files(self):
        put("p1", "same", root=self.dir)
        put("p2", "same", root=self.dir)
        self.assertEqual(len(os.listdir(self.dir)), 2)

    def test_delete_removes_file(self):
        put("p1", "hello", root=self.dir)
        delete("p1", root=self.dir)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "p1.html")))

    def test_blocks_deduplicated(self):
        first = put("p1", "same", root=self.dir)
        second = put("p2", "same", root=self.dir)
        self.assertEqual(len(_block_files(self.dir)), 1)
        self.assertEqual(first["new_blocks"], 1)
        self.assertEqual(second["new_blocks"], 0)
        self.assertEqual(second["reused_blocks"], 1)

    def test_atomic_write_leaves_no_temp_files(self):
        put("p1", "x" * 10000, root=self.dir)
        for sub in ("blocks", "manifests"):
            leftovers = [name for name in os.listdir(os.path.join(self.dir, sub))
                         if name.startswith(".") or name.endswith(".tmp")]
            self.assertEqual(leftovers, [])
        self.assertEqual(get("p1", root=self.dir), "x" * 10000)

    def test_put_existing_page_conflicts(self):
        put("p1", "first", root=self.dir)
        result = put("p1", "second", root=self.dir)
        self.assertTrue(result["conflict"])
        self.assertEqual(get("p1", root=self.dir), "first")

    def test_concurrent_put_single_winner(self):
        results = []
        lock = threading.Lock()

        def worker(index):
            result = put("page", "content-%d" % index, root=self.dir)
            with lock:
                results.append((index, result))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        winners = [i for i, r in results if not r["conflict"]]
        self.assertEqual(len(winners), 1)
        self.assertEqual(get("page", root=self.dir), "content-%d" % winners[0])

    def test_refcount_tracks_references(self):
        put("p1", "shared", root=self.dir)
        put("p2", "shared", root=self.dir)
        self.assertEqual(sorted(_refs(self.dir).values()), [2])
        delete("p1", root=self.dir)
        self.assertEqual(sorted(_refs(self.dir).values()), [1])
        self.assertEqual(len(_block_files(self.dir)), 1)
        self.assertEqual(get("p2", root=self.dir), "shared")

    def test_gc_reclaims_zero_ref_blocks(self):
        put("p1", "shared", root=self.dir)
        put("p2", "shared", root=self.dir)
        put("p3", "unique", root=self.dir)
        delete("p1", root=self.dir)
        delete("p2", root=self.dir)
        result = gc(root=self.dir)
        self.assertEqual(len(result["reclaimed"]), 1)
        self.assertEqual(len(_block_files(self.dir)), 1)
        self.assertEqual(get("p3", root=self.dir), "unique")

    def test_gc_skips_still_referenced_blocks(self):
        put("p1", "keep me", root=self.dir)
        refs_path = os.path.join(self.dir, "blocks", "refs.json")
        with open(refs_path, encoding="utf-8") as handle:
            refs = json.load(handle)
        with open(refs_path, "w", encoding="utf-8") as handle:
            json.dump({key: 0 for key in refs}, handle)
        result = gc(root=self.dir)
        self.assertEqual(result["reclaimed"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(get("p1", root=self.dir), "keep me")
