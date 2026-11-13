import os
import shutil
import tempfile
import unittest

from textnorm import normalize


class NormalizeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "a.csv")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, data):
        open(self.path, "wb").write(data)

    def test_bom_kept(self):
        self.write(b"\xef\xbb\xbfid,name\n")
        self.assertTrue(normalize(self.path)["text"].startswith("\ufeff"))

    def test_crlf_to_lf(self):
        self.write(b"a\r\nb\r\n")
        self.assertEqual(normalize(self.path)["text"], "a\nb\n")

    def test_gbk_as_utf8_fails(self):
        self.write("编号,名称\n".encode("gbk"))
        with self.assertRaises(UnicodeDecodeError):
            normalize(self.path)

    def test_no_line_map(self):
        self.write(b"a\nb\n")
        self.assertNotIn("line_map", normalize(self.path))
