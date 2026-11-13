import os
import shutil
import tempfile
import time
import unittest

from fileindex import changed, index, reset


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
