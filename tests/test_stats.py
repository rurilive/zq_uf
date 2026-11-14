import os
import shutil
import tempfile
import unittest

from partitionstats import prune, stats


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for i in range(2):
            open(os.path.join(self.dir, "p%d.jsonl" % i), "w").write("{}\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_counts_files(self):
        self.assertEqual(stats(self.dir)["files"], 2)

    def test_missing_dataset(self):
        self.assertEqual(stats(os.path.join(self.dir, "nope"))["files"], 0)

    def test_prune_shape(self):
        self.assertEqual(set(prune({})), {"scan", "skip"})
