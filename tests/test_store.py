import os
import shutil
import tempfile
import unittest

from pagestore import delete, get, put


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
