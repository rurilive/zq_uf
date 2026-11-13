import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from fileindex import changed, index, reset

index_mod = __import__("fileindex.index", fromlist=["_index"])


class IndexTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "a.parquet")
        open(self.path, "w").write("AAA")
        index(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_unchanged_file(self):
        self.assertFalse(changed(self.path))

    def test_touch_marks_changed(self):
        stamp = os.path.getmtime(self.path) + 10
        os.utime(self.path, (stamp, stamp))
        self.assertTrue(changed(self.path))

    def test_same_content_new_time_is_change(self):
        open(self.path, "w").write("AAA")
        stamp = time.time() + 10
        os.utime(self.path, (stamp, stamp))
        self.assertTrue(changed(self.path))

    def test_deleted_file_not_cleaned(self):
        os.remove(self.path)
        self.assertIn(os.path.abspath(self.path), __import__("fileindex.index", fromlist=["_index"])._index)


class IncrementalIndexTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "a.parquet")
        open(self.path, "w").write("AAA")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _rewrite(self, path, content):
        open(path, "w").write(content)
        st = os.stat(path)
        os.utime(path, (st.st_atime, st.st_mtime + 10))

    def test_first_scan_counts_added(self):
        stats = index(self.dir)
        self.assertEqual(
            stats,
            {"added": 1, "changed": 0, "deleted": 0, "skipped": 0, "total": 1},
        )

    def test_second_scan_skips_unchanged(self):
        index(self.dir)
        stats = index(self.dir)
        self.assertEqual(
            stats,
            {"added": 0, "changed": 0, "deleted": 0, "skipped": 1, "total": 1},
        )

    def test_unchanged_file_not_rehashed(self):
        index(self.dir)
        calls = []
        original = index_mod._fingerprint
        index_mod._fingerprint = lambda p, c: calls.append(p) or original(p, c)
        try:
            index(self.dir)
        finally:
            index_mod._fingerprint = original
        self.assertEqual(calls, [])

    def test_modified_file_counted_changed(self):
        index(self.dir)
        self._rewrite(self.path, "AAAA")
        stats = index(self.dir)
        self.assertEqual(stats["changed"], 1)
        self.assertEqual(stats["added"], 0)
        self.assertFalse(changed(self.path))

    def test_deleted_file_record_cleaned_on_rescan(self):
        index(self.dir)
        os.remove(self.path)
        stats = index(self.dir)
        self.assertEqual(stats["deleted"], 1)
        self.assertEqual(stats["total"], 0)
        self.assertNotIn(os.path.abspath(self.path), index_mod._index)

    def test_mixed_increment_stats(self):
        other = os.path.join(self.dir, "b.csv")
        open(other, "w").write("BBB")
        keep = os.path.join(self.dir, "c.bin")
        open(keep, "w").write("CCC")
        index(self.dir)
        self._rewrite(self.path, "AAAA")
        os.remove(other)
        open(os.path.join(self.dir, "d.parquet"), "w").write("DDD")
        stats = index(self.dir)
        self.assertEqual(
            stats,
            {"added": 1, "changed": 1, "deleted": 1, "skipped": 1, "total": 3},
        )

    def test_subdirectory_scan_keeps_parent_records(self):
        sub = os.path.join(self.dir, "sub")
        os.mkdir(sub)
        sub_file = os.path.join(sub, "s.csv")
        open(sub_file, "w").write("SSS")
        index(self.dir)
        os.remove(self.path)
        stats = index(sub)
        self.assertEqual(stats["deleted"], 0)
        self.assertIn(os.path.abspath(self.path), index_mod._index)

    def test_empty_file_indexed(self):
        empty = os.path.join(self.dir, "empty.bin")
        open(empty, "w").close()
        stats = index(self.dir)
        self.assertEqual(stats["added"], 2)
        self.assertFalse(changed(empty))

    def test_empty_directory(self):
        os.remove(self.path)
        stats = index(self.dir)
        self.assertEqual(
            stats,
            {"added": 0, "changed": 0, "deleted": 0, "skipped": 0, "total": 0},
        )

    def test_changed_deleted_indexed_file(self):
        index(self.dir)
        os.remove(self.path)
        self.assertTrue(changed(self.path))

    def test_changed_new_file(self):
        new = os.path.join(self.dir, "new.bin")
        open(new, "w").write("NNN")
        self.assertTrue(changed(new))


class ErrorPathTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_index_missing_root(self):
        with self.assertRaises(FileNotFoundError):
            index(os.path.join(self.dir, "nope"))

    def test_index_file_as_root(self):
        path = os.path.join(self.dir, "a.parquet")
        open(path, "w").write("AAA")
        with self.assertRaises(NotADirectoryError):
            index(path)

    def test_index_invalid_chunk_size(self):
        with self.assertRaises(ValueError):
            index(self.dir, chunk_size=0)

    def test_changed_unknown_missing_path(self):
        with self.assertRaises(FileNotFoundError):
            changed(os.path.join(self.dir, "nope"))

    def test_index_broken_symlink_raises(self):
        link = os.path.join(self.dir, "broken")
        os.symlink(os.path.join(self.dir, "missing"), link)
        with self.assertRaises(OSError):
            index(self.dir)


class DemoTest(unittest.TestCase):
    def test_demo_output(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run(
            [sys.executable, "demo.py"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("'added': 3, 'changed': 0, 'deleted': 0, 'skipped': 0", out.stdout)
        self.assertIn("'added': 1, 'changed': 1, 'deleted': 1, 'skipped': 1", out.stdout)
