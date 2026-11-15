import os
import tempfile
import unittest

from attachcheck import fetch, verify


class AttachTest(unittest.TestCase):
    def test_fetch_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.bin")
            self.assertEqual(fetch("https://a/f", path)["bytes"], 4)
            self.assertTrue(os.path.exists(path))

    def test_verify_has_no_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "b.bin")
            fetch("https://a/f", path)
            self.assertIsNone(verify(path)["checksum"])

    def test_not_resumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(fetch("https://a/f", os.path.join(tmp, "c.bin"))["resumed"])
