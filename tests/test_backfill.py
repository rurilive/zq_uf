import unittest

from metricbackfill import apply, plan


class BackfillTest(unittest.TestCase):
    def test_one_shard(self):
        self.assertEqual(len(plan({"start": 1, "end": 9})["shards"]), 1)

    def test_apply_sums(self):
        self.assertEqual(apply(plan({"rows": 3}))["rows"], 3)

    def test_apply_shard_count(self):
        self.assertEqual(apply(plan({"rows": 1}))["shards"], 1)
