import os
import shutil
import tempfile
import unittest

from archivepack import pack, restore


class PackTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_saves_by_url_name(self):
        paths = pack([{"url": "http://shop/a.pdf", "data": b"AAA"}], self.root)
        self.assertTrue(paths[0].endswith("a.pdf"))

    def test_duplicate_name_overwrites(self):
        pack([{"url": "http://shop/a.pdf", "data": b"AAA"},
              {"url": "http://news/a.pdf", "data": b"BBB"}], self.root)
        self.assertEqual(len(restore(self.root)), 1)

    def test_query_string_kept_in_name(self):
        paths = pack([{"url": "http://shop/a.pdf?v=1", "data": b"AAA"}], self.root)
        self.assertTrue(paths[0].endswith("a.pdf?v=1"))

    def test_no_manifest(self):
        pack([{"url": "http://shop/a.pdf", "data": b"AAA"}], self.root)
        self.assertFalse(os.path.exists(os.path.join(self.root, "manifest.json")))
