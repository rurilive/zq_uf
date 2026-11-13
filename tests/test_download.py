import os
import tempfile
import unittest

from filefetch import download


class FilefetchTest(unittest.TestCase):
    def test_writes_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.bin")
            size = download("http://file/a", path, lambda url: b"12345")
            self.assertEqual(size, 5)
            self.assertEqual(open(path, "rb").read(), b"12345")

    def test_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.bin")
            open(path, "wb").write(b"old")
            download("http://file/a", path, lambda url: b"new")
            self.assertEqual(open(path, "rb").read(), b"new")

    def test_empty_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.bin")
            self.assertEqual(download("http://file/a", path, lambda url: b""), 0)
