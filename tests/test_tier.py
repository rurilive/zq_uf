import os
import shutil
import tempfile
import unittest

from tiering import apply, plan


class TierTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for name in ("a.parquet", "b.parquet"):
            open(os.path.join(self.dir, name), "w").write("x")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_all_files_hot(self):
        result = plan(self.dir)
        self.assertEqual({move["tier"] for move in result["moves"]}, {"hot"})

    def test_every_file_listed(self):
        self.assertEqual(len(plan(self.dir)["moves"]), 2)

    def test_apply_moves_nothing(self):
        self.assertEqual(apply(plan(self.dir))["moved"], 0)
