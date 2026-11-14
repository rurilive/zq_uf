import unittest

from backfillplan import plan, run


class PlanTest(unittest.TestCase):
    def test_single_shard(self):
        self.assertEqual(len(plan({"start": 1, "end": 9}, 4)["shards"]), 1)

    def test_workers_ignored(self):
        self.assertEqual(run(plan({"start": 1, "end": 9}), 8)["workers"], 1)

    def test_no_checkpoint(self):
        self.assertNotIn("checkpoint", run(plan({"start": 1, "end": 9})))
