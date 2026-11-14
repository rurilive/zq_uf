import json
import os
import shutil
import tempfile
import unittest

from crawlmanifest import verify, write


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_write_creates_file(self):
        path = write({"rows": 1}, self.dir)
        self.assertTrue(os.path.exists(path))

    def test_verify_ok(self):
        self.assertTrue(verify(write({"rows": 1}, self.dir))["ok"])

    def test_verify_corrupt(self):
        path = os.path.join(self.dir, "bad.json")
        open(path, "w").write("{")
        self.assertFalse(verify(path)["ok"])
