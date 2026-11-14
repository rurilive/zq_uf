import hashlib
import os
import shutil
import tempfile
import threading
import unittest

from filededup import Store, references, report


def digest(content):
    return hashlib.sha256(content).hexdigest()


class ContentStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.root = os.path.join(self.dir, "store")
        self.store = Store(self.root)
        self.a = os.path.join(self.dir, "a.txt")
        self.b = os.path.join(self.dir, "b.txt")
        self.c = os.path.join(self.dir, "c.txt")
        with open(self.a, "w") as handle:
            handle.write("same")
        with open(self.b, "w") as handle:
            handle.write("same")
        with open(self.c, "w") as handle:
            handle.write("other")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_same_content_reuses_single_object(self):
        first = self.store.add(self.a)
        second = self.store.add(self.b)
        self.assertEqual(first["action"], "added")
        self.assertEqual(second["action"], "reused")
        self.assertEqual(first["object_id"], second["object_id"])
        self.assertEqual(first["object_id"], digest(b"same"))
        objects = os.listdir(os.path.join(self.root, "objects"))
        self.assertEqual(objects, [first["object_id"]])

    def test_different_content_creates_separate_object(self):
        first = self.store.add(self.a)
        third = self.store.add(self.c)
        self.assertNotEqual(first["object_id"], third["object_id"])
        self.assertEqual(len(os.listdir(os.path.join(self.root, "objects"))), 2)

    def test_reference_count_and_lookup_by_object(self):
        self.store.add(self.a)
        self.store.add(self.b)
        object_id = digest(b"same")
        self.assertEqual(
            self.store.references(object_id),
            sorted([os.path.abspath(self.a), os.path.abspath(self.b)]),
        )
        self.assertEqual(
            references(path=self.a, root=self.root),
            sorted([os.path.abspath(self.a), os.path.abspath(self.b)]),
        )
        entry = next(item for item in report(self.root)["objects"] if item["object_id"] == object_id)
        self.assertEqual(entry["refcount"], 2)

    def test_remove_keeps_shared_object_until_refcount_zero(self):
        first = self.store.add(self.a)
        self.store.add(self.b)
        object_id = first["object_id"]
        object_path = first["object_path"]

        result = self.store.remove(self.a)
        self.assertEqual(result["reclaimed"], [])
        self.assertTrue(os.path.exists(object_path))
        with open(object_path, "rb") as handle:
            self.assertEqual(handle.read(), b"same")
        self.assertEqual(self.store.references(object_id), [os.path.abspath(self.b)])

        final = self.store.remove(self.b)
        self.assertEqual(final["reclaimed"], [object_id])
        self.assertEqual(self.store.references(object_id), [])
        self.assertFalse(os.path.exists(object_path))
        self.assertEqual(os.listdir(self.root), [])

    def test_remove_unknown_path_is_safe(self):
        result = self.store.remove(self.a)
        self.assertEqual(result["action"], "unknown")
        self.assertEqual(result["reclaimed"], [])

    def test_re_add_changed_content_reclaims_old_object(self):
        self.store.add(self.a)
        old_id = digest(b"same")
        with open(self.a, "w") as handle:
            handle.write("changed")
        result = self.store.add(self.a)
        self.assertEqual(result["action"], "remapped")
        self.assertEqual(result["reclaimed"], [old_id])
        self.assertFalse(os.path.exists(os.path.join(self.root, "objects", old_id)))
        self.assertEqual(self.store.references(result["object_id"]), [os.path.abspath(self.a)])

    def test_concurrent_adds_share_one_object(self):
        paths = []
        for index in range(20):
            path = os.path.join(self.dir, "p%d.txt" % index)
            with open(path, "w") as handle:
                handle.write("same")
            paths.append(path)

        threads = [threading.Thread(target=self.store.add, args=(path,)) for path in paths]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        object_id = digest(b"same")
        self.assertEqual(os.listdir(os.path.join(self.root, "objects")), [object_id])
        self.assertEqual(len(self.store.references(object_id)), 20)
        snapshot = report(self.root)
        self.assertEqual(snapshot["orphans"], [])
        self.assertEqual(snapshot["objects"][0]["refcount"], 20)

    def test_concurrent_add_and_remove_ends_consistent(self):
        paths = []
        for index in range(30):
            path = os.path.join(self.dir, "q%d.txt" % index)
            with open(path, "w") as handle:
                handle.write("same")
            paths.append(path)

        errors = []

        def worker(path):
            try:
                self.store.add(path)
                self.store.remove(path)
            except Exception as exc:  # pragma: no cover - 失败时记录
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(path,)) for path in paths]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(self.store.references(digest(b"same")), [])
        self.assertEqual(report(self.root)["objects"], [])
        self.assertEqual(report(self.root)["orphans"], [])
        objects_dir = os.path.join(self.root, "objects")
        self.assertFalse(os.path.exists(objects_dir))
        self.assertEqual(os.listdir(self.root), [])


if __name__ == "__main__":
    unittest.main()
