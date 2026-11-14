import os
import shutil
import tempfile
import unittest

from ckptgen import load, save


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_save_load(self):
        save("t1", {"cursor": 3}, root=self.dir)
        self.assertEqual(load("t1", root=self.dir)["cursor"], 3)

    def test_overwrite_loses_old(self):
        save("t1", {"cursor": 1}, root=self.dir)
        save("t1", {"cursor": 2}, root=self.dir)
        self.assertEqual(load("t1", 1, root=self.dir)["cursor"], 2)

    def test_no_generation_field(self):
        save("t1", {"cursor": 1}, root=self.dir)
        self.assertNotIn("generation", load("t1", root=self.dir))
