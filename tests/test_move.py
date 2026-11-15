import threading
import unittest

from partmove2 import (
    Manifest,
    Migration,
    MigrationError,
    Store,
)


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Fixture(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = Store()
        self.engine = Migration(store=self.store, clock=self.clock,
                                retention_seconds=600)
        self.engine.write_many("orders-11", {"a": 1, "b": 2, "c": 3})

    def target(self):
        return "cold/orders-11"


class DualWriteTest(Fixture):
    def test_writes_go_to_both_paths_while_migrating(self):
        self.engine.start("orders-11", self.target())
        self.engine.write("orders-11", "d", 4)
        self.assertEqual(self.engine.read("orders-11"),
                         {"a": 1, "b": 2, "c": 3, "d": 4})
        self.assertEqual(self.store.rows(self.target()), {"d": 4})
        self.assertGreater(self.engine.progress("orders-11")["position"], 0)

    def test_delete_is_mirrored(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        self.assertTrue(self.engine.delete("orders-11", "b"))
        self.assertNotIn("b", self.engine.read("orders-11"))
        self.assertNotIn("b", self.store.rows(self.target()))

    def test_position_advances_as_backfill_progresses(self):
        self.engine.start("orders-11", self.target())
        first = self.engine.catch_up("orders-11", limit=1)
        self.assertGreaterEqual(first["position"], 1)
        self.assertGreater(len(first["history"]), 0)
        self.assertTrue(first["history"][-1]["phase"])


class CatchUpAndCutoverTest(Fixture):
    def test_cutover_refused_before_catch_up(self):
        self.engine.start("orders-11", self.target())
        result = self.engine.cutover("orders-11")
        self.assertFalse(result["switched"])
        self.assertIn("尚未追平", result["error"])
        self.assertEqual(self.engine.get_manifest().resolve("orders-11"),
                         "orders-11")

    def test_cutover_after_catch_up_switches_manifest(self):
        self.engine.start("orders-11", self.target())
        progress = self.engine.catch_up("orders-11")
        self.assertTrue(progress["caught_up"])
        self.assertEqual(progress["lag"], 0)
        result = self.engine.cutover("orders-11")
        self.assertTrue(result["switched"])
        self.assertEqual(result["phase"], "switched")
        self.assertEqual(self.engine.get_manifest().resolve("orders-11"),
                         self.target())

    def test_progress_reports_stage_position_and_counts(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11", limit=1)
        p = self.engine.progress("orders-11")
        self.assertEqual(p["stage"], p["phase"])
        self.assertIn(p["phase"], ("dual_write", "caught_up"))
        self.assertEqual(p["source_rows"], 3)
        self.assertEqual(p["target_rows"] + p["lag"], 3)
        self.assertIsNotNone(p["position"])
        self.assertEqual(set(p), set(self.engine.progress("orders-11")))

    def test_switch_failure_stays_on_old_path(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        self.engine.fail_switch = True
        result = self.engine.cutover("orders-11")
        self.assertFalse(result["switched"])
        self.assertIn("切换失败", result["error"])
        self.assertEqual(result["phase"], "switch_failed")
        self.assertEqual(self.engine.get_manifest().resolve("orders-11"),
                         "orders-11")
        self.assertTrue(self.store.exists(self.target()))
        # 失败后读写仍走旧路径且正常。
        self.engine.write("orders-11", "e", 5)
        self.assertEqual(self.engine.read("orders-11")["e"], 5)


class VerifyTest(Fixture):
    def test_verify_reports_row_counts_on_both_sides(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        check = self.engine.verify("orders-11")
        self.assertTrue(check["ok"])
        self.assertEqual(check["source_rows"], 3)
        self.assertEqual(check["target_rows"], 3)
        self.assertEqual(check["diff"]["different"], [])

    def test_verify_detects_extra_row_and_blocks_switch(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        self.store.put(self.target(), "z", 99)
        check = self.engine.verify("orders-11")
        self.assertFalse(check["ok"])
        self.assertEqual(check["source_rows"], 3)
        self.assertEqual(check["target_rows"], 4)
        self.assertIn("z", check["diff"]["extra_in_target"])
        result = self.engine.cutover("orders-11")
        self.assertFalse(result["switched"])
        self.assertEqual(result["verify"]["source_rows"], 3)
        self.assertEqual(result["verify"]["target_rows"], 4)
        self.assertEqual(self.engine.get_manifest().resolve("orders-11"),
                         "orders-11")

    def test_verify_detects_content_mismatch(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        self.store.put(self.target(), "a", 999)
        check = self.engine.verify("orders-11")
        self.assertFalse(check["ok"])
        self.assertFalse(check["content_match"])
        self.assertIn("a", check["diff"]["different"])


class RollbackTest(Fixture):
    def test_rollback_all_is_all_or_nothing(self):
        self.engine.write_many("orders-12", {"x": 1})
        for p, t in (("orders-11", self.target()), ("orders-12", "cold/orders-12")):
            self.engine.start(p, t)
            self.engine.catch_up(p)
            self.engine.cutover(p)
        # 让第二个分区变为不可回滚
        self.engine.write("orders-12", "y", 2)
        self.clock.advance(601)
        self.engine.write("orders-12", "z", 3)
        with self.assertRaises(MigrationError):
            self.engine.rollback()
        # 一个都没回滚，两个分区的清单都仍指向新路径
        self.assertEqual(self.engine.get_manifest().resolve("orders-11"),
                         self.target())
        self.assertEqual(self.engine.get_manifest().resolve("orders-12"),
                         "cold/orders-12")

    def test_retry_after_switch_failure(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        self.engine.fail_switch = True
        self.assertFalse(self.engine.cutover("orders-11")["switched"])
        self.engine.fail_switch = False
        retry = self.engine.start("orders-11", self.target())
        self.assertIn(retry["phase"], ("dual_write", "caught_up"))
        self.assertEqual(retry["attempt"], 2)
        self.assertEqual(retry["retry_of"], "switch_failed")
        self.assertTrue(self.engine.move("orders-11", self.target())["switch"]["switched"])

    def test_verify_before_catch_up_reports_both_row_counts(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11", limit=1)
        check = self.engine.verify("orders-11")
        self.assertFalse(check["ok"])
        self.assertEqual(check["source_rows"], 3)
        self.assertEqual(check["target_rows"], 1)
        self.assertEqual(check["lag"], 2)
        self.assertIn("b", check["diff"]["missing_in_target"])

    def test_history_records_phases_in_order(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        self.engine.cutover("orders-11")
        self.engine.rollback("orders-11")
        phases = [h["phase"] for h in self.engine.progress("orders-11")["history"]]
        self.assertEqual(phases, ["dual_write", "caught_up", "switched", "rolled_back"])
        self.assertTrue(all("at" in h for h in
                            self.engine.progress("orders-11")["history"]))

    def test_rollback_restores_old_path_and_retains_target(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        self.assertTrue(self.engine.cutover("orders-11")["switched"])
        result = self.engine.rollback("orders-11")
        self.assertTrue(result["rolled_back"])
        self.assertIn("orders-11", result["restored"])
        self.assertEqual(self.engine.get_manifest().resolve("orders-11"),
                         "orders-11")
        self.assertIn("orders-11", result["retained"])
        self.assertTrue(self.store.exists(self.target()))
        entry = result["rollback_manifest"][0]
        self.assertEqual(entry["target"], self.target())
        self.assertIsNotNone(entry["retained_until"])

    def test_target_kept_until_retention_expires_then_purged(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        self.engine.cutover("orders-11")
        self.engine.rollback("orders-11")
        self.assertEqual(self.engine.purge()["count"], 0)
        self.clock.advance(601)
        purged = self.engine.purge()
        self.assertEqual(purged["count"], 1)
        self.assertFalse(self.store.exists(self.target()))
        self.assertTrue(self.store.exists("orders-11"))

    def test_rollback_of_unswitched_migration_is_safe(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11", limit=1)
        result = self.engine.rollback("orders-11")
        self.assertTrue(result["rolled_back"])
        self.assertEqual(result["restored"], [])
        self.assertEqual(self.engine.get_manifest().resolve("orders-11"),
                         "orders-11")
        self.assertTrue(self.store.exists(self.target()))

    def test_rollback_without_migration_is_empty(self):
        result = self.engine.rollback()
        self.assertFalse(result["rolled_back"])
        self.assertEqual(result["restored"], [])

    def test_retry_after_rollback(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        self.engine.cutover("orders-11")
        self.engine.rollback("orders-11")
        self.engine.start("orders-11", self.target())
        report = self.engine.move("orders-11", self.target())
        self.assertTrue(report["switch"]["switched"])
        self.assertEqual(self.engine.get_manifest().resolve("orders-11"),
                         self.target())

    def test_cutover_after_rollback_requires_restart(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        self.engine.cutover("orders-11")
        self.engine.rollback("orders-11")
        result = self.engine.cutover("orders-11")
        self.assertFalse(result["switched"])
        self.assertEqual(self.engine.get_manifest().resolve("orders-11"),
                         "orders-11")

    def test_writes_after_switch_are_not_lost_on_rollback(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        self.engine.cutover("orders-11")
        self.engine.write("orders-11", "late", 42)
        self.engine.delete("orders-11", "a")
        self.engine.rollback("orders-11")
        rows = self.engine.read("orders-11")
        self.assertEqual(rows["late"], 42)
        self.assertNotIn("a", rows)

    def test_rollback_refused_once_source_retention_expired(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        self.engine.cutover("orders-11")
        self.engine.write("orders-11", "late", 1)   # 触发保留期检查
        self.clock.advance(601)
        self.engine.write("orders-11", "later", 2)
        with self.assertRaises(MigrationError):
            self.engine.rollback("orders-11")
        self.assertEqual(self.engine.get_manifest().resolve("orders-11"),
                         self.target())

    def test_source_kept_until_finalize_after_switch(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        switched = self.engine.cutover("orders-11")
        self.assertIsNotNone(switched["source_retained_until"])
        self.assertTrue(self.store.exists("orders-11"))
        with self.assertRaises(MigrationError):
            self.engine.finalize("orders-11")
        self.clock.advance(601)
        dropped = self.engine.finalize("orders-11")
        self.assertEqual(dropped["dropped"], "orders-11")
        self.assertFalse(self.store.exists("orders-11"))
        # 切换后写只进新路径。
        self.engine.write("orders-11", "n", 1)
        self.assertEqual(self.engine.get("orders-11", "n"), 1)


class MoveTest(Fixture):
    def test_move_returns_phase_position_switch_and_rollback_manifest(self):
        report = self.engine.move("orders-11", self.target())
        self.assertEqual(report["stage"], "switched")
        self.assertIsNotNone(report["position"])
        self.assertTrue(report["switch"]["switched"])
        self.assertEqual(report["switch"]["version"], 1)
        self.assertEqual(len(report["rollback_manifest"]), 1)
        self.assertEqual(report["rollback_manifest"][0]["source"], "orders-11")

    def test_move_idempotent_for_same_target(self):
        self.engine.move("orders-11", self.target())
        again = self.engine.move("orders-11", self.target())
        self.assertTrue(again["switch"]["switched"])

    def test_move_rejects_different_target(self):
        self.engine.start("orders-11", self.target())
        with self.assertRaises(MigrationError):
            self.engine.start("orders-11", "other/path")

    def test_unknown_partition_progress_is_idle(self):
        p = self.engine.progress("nope")
        self.assertEqual(p["stage"], "idle")
        self.assertEqual(p["source_rows"], 0)

    def test_move_reports_switch_failure_phase(self):
        self.engine.fail_switch = True
        report = self.engine.move("orders-11", self.target())
        self.assertEqual(report["stage"], "switch_failed")
        self.assertFalse(report["switched"])
        self.assertFalse(report["switch"]["switched"])
        self.assertEqual(report["current_path"], "orders-11")

    def test_manifest_version_increments_once_on_switch(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        before = self.engine.version
        self.engine.cutover("orders-11")
        self.assertEqual(self.engine.version, before + 1)

    def test_new_writes_between_catch_up_and_cutover_still_replicated(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        self.engine.write("orders-11", "f", 6)
        self.assertTrue(self.engine.cutover("orders-11")["switched"])
        self.assertEqual(self.engine.read("orders-11")["f"], 6)


class ConcurrentReadTest(Fixture):
    def test_reads_never_see_partial_partition_during_switch(self):
        self.engine.start("orders-11", self.target())
        self.engine.catch_up("orders-11")
        seen = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                seen.append(len(self.engine.read("orders-11")))

        t = threading.Thread(target=reader)
        t.start()
        try:
            for _ in range(50):
                self.engine.cutover("orders-11")
                self.engine.rollback("orders-11")
        finally:
            stop.set()
            t.join()
        self.assertTrue(seen)
        self.assertEqual(set(seen), {3})

    def test_concurrent_writes_and_backfill_converge(self):
        big = {"k%d" % i: i for i in range(200)}
        self.engine.write_many("orders-11", big)
        self.engine.start("orders-11", self.target())

        def writer(start):
            for i in range(start, start + 50):
                self.engine.write("orders-11", "w%d" % i, i)

        threads = [threading.Thread(target=writer, args=(n,)) for n in (0, 60)]
        for t in threads:
            t.start()
        for _ in range(10):
            self.engine.catch_up("orders-11", limit=20)
        for t in threads:
            t.join()
        self.engine.catch_up("orders-11")
        check = self.engine.verify("orders-11")
        self.assertTrue(check["ok"], check["diff"])
        self.assertTrue(self.engine.cutover("orders-11")["switched"])


class RehearseTest(Fixture):
    def test_rehearse_verifies_query_continuity_without_switching(self):
        report = self.engine.rehearse("orders-11", self.target())
        checks = report["checks"]
        self.assertTrue(checks["dual_write"])
        self.assertTrue(checks["caught_up"])
        self.assertTrue(checks["verified"])
        self.assertTrue(checks["switch_simulated"])
        self.assertFalse(checks["query_interrupted"])
        self.assertEqual(self.engine.get_manifest().resolve("orders-11"),
                         "orders-11")

    def test_rehearse_on_low_traffic_partition_then_move(self):
        self.engine.write_many("orders-12", {"x": 1})
        report = self.engine.rehearse("orders-12", "cold/orders-12")
        self.assertEqual(report["partition"], "orders-12")
        self.assertIn("position", report)
        self.engine.move("orders-12", "cold/orders-12")

    def test_rehearse_refuses_already_switched_partition(self):
        self.engine.move("orders-11", self.target())
        with self.assertRaises(MigrationError):
            self.engine.rehearse("orders-11", self.target())


class StoreAndManifestTest(unittest.TestCase):
    def test_manifest_register_does_not_overwrite(self):
        m = Manifest()
        m.register("p", "a")
        m.register("p", "b")
        self.assertEqual(m.resolve("p"), "a")

    def test_store_digest_is_order_insensitive(self):
        s = Store()
        s.put("p", "a", 1)
        s.put("p", "b", 2)
        d1 = s.digest("p")
        s2 = Store()
        s2.put("p", "b", 2)
        s2.put("p", "a", 1)
        self.assertEqual(d1, s2.digest("p"))

    def test_manifest_switch_is_single_assignment(self):
        m = Manifest()
        m.register("p", "a")
        v = m.switch("p", "b")
        self.assertEqual(v, 1)
        self.assertEqual(m.resolve("p"), "b")


if __name__ == "__main__":
    unittest.main()
