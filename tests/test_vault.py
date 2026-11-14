import json
import os
import shutil
import tempfile
import unittest

from sessionvault import load, save


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_roundtrip(self):
        save("a", {"cookie": "sid=1"}, root=self.dir)
        self.assertEqual(load("a", root=self.dir)["cookie"], "sid=1")

    def test_plaintext_on_disk(self):
        save("a", {"cookie": "sid=1"}, root=self.dir)
        raw = open(os.path.join(self.dir, "a.json"), encoding="utf-8").read()
        self.assertIn("sid=1", raw)

    def test_no_key_version(self):
        save("a", {"cookie": "sid=1"}, root=self.dir)
        self.assertNotIn("key_version", load("a", root=self.dir))
