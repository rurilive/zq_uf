import os
import shutil
import tempfile
import unittest

from lakechecksum import repair, verify


class ChecksumTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "a.bin")
        open(self.path, "wb").write(b"abc")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_sha_length(self):
        self.assertEqual(len(verify(self.path)["sha256"]), 64)

    def test_single_block(self):
        self.assertEqual(verify(self.path)["blocks"], 1)

    def test_repair_zero(self):
        self.assertEqual(repair("p1")["repaired"], 0)
