import os
import shutil
import tempfile
import threading
import unittest

from filededup import add, remove
from filededup import objects, refs


class DedupTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.root = os.path.join(self.dir, "store")
        self.a = os.path.join(self.dir, "a.txt")
        self.b = os.path.join(self.dir, "b.txt")
        open(self.a, "w").write("same")
        open(self.b, "w").write("same")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_same_content_two_copies(self):
        add(self.a, self.root)
        add(self.b, self.root)
        self.assertEqual(len(os.listdir(self.root)), 2)

    def test_remove_deletes_copy(self):
        add(self.a, self.root)
        remove(self.a, self.root)
        self.assertEqual(os.listdir(self.root), [])

    def test_no_refcount(self):
        self.assertNotIn("refs", add(self.a, self.root))


class ContentAddressedTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.root = os.path.join(self.dir, "store")
        self.objects_dir = self.root + ".objects"
        self.a = os.path.join(self.dir, "a.txt")
        self.b = os.path.join(self.dir, "b.txt")
        open(self.a, "w").write("same")
        open(self.b, "w").write("same")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def digest(self):
        return next(iter(objects(self.root)))

    def test_same_content_shared_object(self):
        info_a = add(self.a, self.root)
        info_b = add(self.b, self.root)
        self.assertTrue(info_a["created"])
        self.assertFalse(info_b["created"])
        self.assertEqual(info_a["object"], info_b["object"])
        self.assertEqual(len(os.listdir(self.objects_dir)), 1)
        self.assertTrue(os.path.samefile(info_a["path"], info_b["path"]))

    def test_refcount_tracks_paths(self):
        add(self.a, self.root)
        add(self.b, self.root)
        digest = self.digest()
        self.assertEqual(objects(self.root)[digest]["refcount"], 2)
        self.assertEqual(refs(digest, self.root), sorted([self.a, self.b]))
        result = remove(self.a, self.root)
        self.assertFalse(result["reclaimed"])
        self.assertEqual(result["refcount"], 1)
        self.assertEqual(result["paths"], [self.b])
        self.assertEqual(refs(digest, self.root), [self.b])

    def test_reclaim_only_at_zero(self):
        add(self.a, self.root)
        add(self.b, self.root)
        digest = self.digest()
        obj_path = objects(self.root)[digest]["path"]
        remove(self.a, self.root)
        self.assertTrue(os.path.exists(obj_path))
        self.assertEqual(open(os.path.join(self.root, "b.txt")).read(), "same")
        result = remove(self.b, self.root)
        self.assertTrue(result["reclaimed"])
        self.assertFalse(os.path.exists(obj_path))
        self.assertEqual(objects(self.root), {})
        self.assertEqual(refs(digest, self.root), [])

    def test_concurrent_add_remove(self):
        keep = os.path.join(self.dir, "keep.txt")
        open(keep, "w").write("same")
        add(keep, self.root)
        digest = self.digest()
        errors = []

        def worker(index):
            path = os.path.join(self.dir, "w%d.txt" % index)
            open(path, "w").write("same")
            try:
                for _ in range(20):
                    add(path, self.root)
                    remove(path, self.root)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        remaining = objects(self.root)
        self.assertEqual(list(remaining), [digest])
        self.assertEqual(remaining[digest]["refcount"], 1)
        self.assertEqual(refs(digest, self.root), [keep])
        self.assertEqual(open(os.path.join(self.root, "keep.txt")).read(), "same")
