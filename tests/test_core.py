import os
import tempfile
import unittest

from snapver import Snapshot
from snapver.core import SnapshotError


class SnapverTest(unittest.TestCase):
    def test_create_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "data")
            store = os.path.join(tmp, "snaps")
            os.makedirs(root)
            open(os.path.join(root, "a.txt"), "w").write("v1")
            snap = Snapshot(root, store)
            snap.create("s1")
            open(os.path.join(root, "a.txt"), "w").write("v2")
            snap.restore("s1")
            self.assertEqual(open(os.path.join(root, "a.txt")).read(), "v1")

    def _make(self, tmp):
        root = os.path.join(tmp, "data")
        os.makedirs(root)
        return root, Snapshot(root, os.path.join(tmp, "snaps"))

    def test_consistent_view_skips_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, snap = self._make(tmp)
            open(os.path.join(root, "keep.txt"), "w").write("ok")
            open(os.path.join(root, "partial.tmp"), "w").write("half")
            open(os.path.join(root, "swap.swp"), "w").write("half")
            meta = snap.create("s1")
            self.assertEqual(sorted(meta["files"]), ["keep.txt"])
            os.remove(os.path.join(root, "keep.txt"))
            self.assertEqual(snap.restore("s1"), 1)
            self.assertEqual(os.listdir(root), ["keep.txt"])

    def test_metadata_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, snap = self._make(tmp)
            open(os.path.join(root, "a.txt"), "w").write("v1")
            meta = snap.create("s1")
            entry = meta["files"]["a.txt"]
            self.assertEqual(entry["size"], 2)
            self.assertEqual(len(entry["sha256"]), 64)
            self.assertTrue(meta["created_at"])
            self.assertTrue(os.path.exists(
                os.path.join(snap.manifests, "s1.json")))

    def test_dedup_and_refcount(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, snap = self._make(tmp)
            open(os.path.join(root, "a.txt"), "w").write("same")
            open(os.path.join(root, "b.txt"), "w").write("same")
            snap.create("s1")
            self.assertEqual(len(os.listdir(snap.objects)), 1)
            snap.create("s2")
            self.assertEqual(len(os.listdir(snap.objects)), 1)
            self.assertEqual(list(snap._load_refs().values()), [4])
            snap.delete("s1")
            self.assertEqual(len(os.listdir(snap.objects)), 1)
            snap.delete("s2")
            self.assertEqual(len(os.listdir(snap.objects)), 0)

    def test_restore_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, snap = self._make(tmp)
            open(os.path.join(root, "a.txt"), "w").write("v1")
            snap.create("s1")
            blob = os.path.join(snap.objects, os.listdir(snap.objects)[0])
            open(blob, "w").write("corrupted")
            open(os.path.join(root, "a.txt"), "w").write("v2")
            with self.assertRaises(SnapshotError):
                snap.restore("s1")
            self.assertEqual(open(os.path.join(root, "a.txt")).read(), "v2")

    def test_restore_rejects_missing_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, snap = self._make(tmp)
            open(os.path.join(root, "a.txt"), "w").write("v1")
            snap.create("s1")
            os.remove(os.path.join(snap.objects, os.listdir(snap.objects)[0]))
            with self.assertRaises(SnapshotError):
                snap.restore("s1")
