import os
import shutil
import tempfile
import unittest

from auditchain import append, verify


class ChainTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "audit.log")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_append(self):
        append({"id": 1}, self.path)
        self.assertEqual(len(open(self.path, encoding="utf-8").readlines()), 1)

    def test_verify_always_true(self):
        append({"id": 1}, self.path)
        self.assertTrue(verify(path=self.path)["ok"])

    def test_no_hash_chain(self):
        append({"id": 1}, self.path)
        self.assertNotIn("prev_hash", open(self.path, encoding="utf-8").read())
