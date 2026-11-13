import unittest

from shardbalance import SITES, WORKERS, assign, rebalance


class ShardingTest(unittest.TestCase):
    def test_assign_returns_worker(self):
        self.assertIn(assign("shop"), WORKERS)

    def test_same_site_stable(self):
        self.assertEqual(assign("shop"), assign("shop"))

    def test_rebalance_covers_all_sites(self):
        self.assertEqual(sorted(rebalance()), sorted(SITES))

    def test_adding_worker_moves_most_sites(self):
        before = rebalance(["m1", "m2", "m3"])
        after = rebalance(["m1", "m2", "m3", "m4"])
        moved = [site for site in SITES if before[site] != after[site]]
        self.assertGreater(len(moved), 1)

    def test_empty_workers_raise(self):
        with self.assertRaises(ValueError):
            assign("shop", [])
