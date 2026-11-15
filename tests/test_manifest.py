import os
import tempfile
import unittest

from manifestcheck import verify, write


class ManifestTest(unittest.TestCase):
    def test_write_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.json")
            self.assertEqual(write({"path": path, "files": ["a"]})["files"], 1)

    def test_no_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.json")
            write({"path": path, "files": []})
            self.assertIsNone(verify(path)["checksum"])

    def test_verify_only_existence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.json")
            write({"path": path, "files": []})
            self.assertTrue(verify(path)["ok"])
