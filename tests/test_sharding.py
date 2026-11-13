import threading
import time
import unittest

from shardbalance import (
    SITES,
    WORKERS,
    DrainTimeoutError,
    DuplicateExecutionError,
    ShardBalanceError,
    acquire,
    assign,
    rebalance,
    release,
    running_tasks,
    sharding,
)


class ShardingTest(unittest.TestCase):
    def setUp(self):
        sharding._reset_state()

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


class ConsistentHashTest(unittest.TestCase):
    def setUp(self):
        sharding._reset_state()

    def test_assign_stable_after_repeated_calls(self):
        self.assertEqual(assign("news"), assign("news"))

    def test_virtual_nodes_spread_sites_across_workers(self):
        table = rebalance(["m1", "m2", "m3"])
        owners = set(table.values())
        self.assertGreaterEqual(len(owners), 2)

    def test_scale_up_only_moves_sites_to_new_worker(self):
        before = rebalance(["m1", "m2", "m3"])
        after = rebalance(["m1", "m2", "m3", "m4"])
        moved = [site for site in SITES if before[site] != after[site]]
        self.assertTrue(moved)
        for site in moved:
            self.assertEqual(after[site], "m4")
        for site in SITES:
            if site not in moved:
                self.assertEqual(before[site], after[site])

    def test_scale_down_only_moves_sites_from_removed_worker(self):
        before = rebalance(["m1", "m2", "m3", "m4"])
        after = rebalance(["m1", "m2", "m3"])
        moved = [site for site in SITES if before[site] != after[site]]
        self.assertTrue(moved)
        for site in moved:
            self.assertEqual(before[site], "m4")
        for site in SITES:
            if before[site] != "m4":
                self.assertEqual(before[site], after[site])

    def test_minimal_migration_keeps_table_unchanged_for_same_topology(self):
        before = rebalance(["m1", "m2", "m3"])
        again = rebalance(["m1", "m2", "m3"])
        self.assertEqual(again.affected_sites, 0)
        self.assertEqual(again.migrations, [])
        self.assertEqual(dict(before), dict(again))

    def test_rebalance_reports_migration_list_and_counts(self):
        before = rebalance(["m1", "m2", "m3"])
        result = rebalance(["m1", "m2", "m3", "m4"])
        self.assertEqual(
            sorted(result.migrations),
            sorted(
                (site, before[site], "m4")
                for site in SITES
                if before[site] != result[site]
            ),
        )
        self.assertEqual(result.affected_sites, len(result.migrations))
        self.assertEqual(result.pending_tasks, {})


class TaskGuardTest(unittest.TestCase):
    def setUp(self):
        sharding._reset_state()

    def test_duplicate_execution_rejected(self):
        owner = assign("shop")
        acquire("shop", owner)
        with self.assertRaises(DuplicateExecutionError):
            acquire("shop", owner)
        self.assertEqual(running_tasks(), {"shop": owner})
        release("shop", owner)
        self.assertNotIn("shop", running_tasks())

    def test_non_owner_cannot_acquire(self):
        owner = assign("blog")
        other = next(worker for worker in WORKERS if worker != owner)
        with self.assertRaises(ShardBalanceError):
            acquire("blog", other)

    def test_release_requires_running_task_and_owner(self):
        with self.assertRaises(ShardBalanceError):
            release("video", assign("video"))
        owner = assign("video")
        acquire("video", owner)
        other = next(worker for worker in WORKERS if worker != owner)
        with self.assertRaises(ShardBalanceError):
            release("video", other)
        release("video", owner)

    def test_rebalance_waits_for_running_tasks_before_switching(self):
        before = rebalance(["m1", "m2", "m3"])
        moving_site = next(
            site
            for site in SITES
            if before[site] != assign(site, ["m1", "m2", "m3", "m4"])
        )
        acquire(moving_site, before[moving_site])

        finished = threading.Event()
        holder = {}

        def run_rebalance():
            holder["result"] = rebalance(
                ["m1", "m2", "m3", "m4"], timeout=10
            )
            finished.set()

        thread = threading.Thread(target=run_rebalance)
        thread.start()
        self.assertFalse(finished.wait(0.2))
        self.assertEqual(assign(moving_site), before[moving_site])

        release(moving_site, before[moving_site])
        self.assertTrue(finished.wait(2))
        thread.join()
        result = holder["result"]
        self.assertEqual(assign(moving_site), result[moving_site])
        self.assertNotIn(moving_site, result.pending_tasks)

    def test_rebalance_timeout_keeps_old_table_and_reports_pending(self):
        before = rebalance(["m1", "m2", "m3"])
        moving_site = next(
            site
            for site in SITES
            if before[site] != assign(site, ["m1", "m2", "m3", "m4"])
        )
        acquire(moving_site, before[moving_site])
        started = time.monotonic()
        with self.assertRaises(DrainTimeoutError) as ctx:
            rebalance(["m1", "m2", "m3", "m4"], timeout=0.1)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(
            ctx.exception.pending_tasks,
            {moving_site: before[moving_site]},
        )
        self.assertEqual(assign(moving_site), before[moving_site])
        release(moving_site, before[moving_site])

    def test_rebalance_ignores_unrelated_running_tasks(self):
        before = rebalance(["m1", "m2", "m3"])
        staying_site = next(
            site
            for site in SITES
            if before[site] == assign(site, ["m1", "m2", "m3", "m4"])
        )
        acquire(staying_site, before[staying_site])
        result = rebalance(["m1", "m2", "m3", "m4"], timeout=1)
        self.assertEqual(result.pending_tasks, {})
        release(staying_site, before[staying_site])

    def test_invalid_arguments_raise_explicit_errors(self):
        with self.assertRaises(ValueError):
            assign("shop", [])
        with self.assertRaises(ValueError):
            rebalance(["m1", "m1"])
        with self.assertRaises(TypeError):
            rebalance("m1")
        with self.assertRaises(ValueError):
            assign("")
        with self.assertRaises(ValueError):
            rebalance(timeout=-1)
