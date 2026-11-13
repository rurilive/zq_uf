import os
import tempfile
import unittest

from compact import compact


class CompactTest(unittest.TestCase):
    def test_merges_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.txt"), "w").write("1")
            open(os.path.join(tmp, "b.txt"), "w").write("2")
            out = compact(tmp)
            self.assertEqual(open(out).read(), "1\n2")

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = compact(tmp)
            self.assertEqual(open(out).read(), "")
