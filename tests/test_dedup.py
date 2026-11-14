import os
import shutil
import tempfile
import unittest

from filededup import add, remove


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
