import unittest

from schemamigrate import current, downgrade, upgrade


class MigrateTest(unittest.TestCase):
    def setUp(self):
        from schemamigrate import migrate
        migrate._version["value"] = 1

    def test_upgrade_sets_version(self):
        upgrade(2)
        self.assertEqual(current(), 2)

    def test_downgrade_not_supported(self):
        with self.assertRaises(NotImplementedError):
            downgrade(1)

    def test_no_checksum(self):
        self.assertNotIn("checksum", upgrade(3))


class BidirectionalMigrateTest(unittest.TestCase):
    def setUp(self):
        from schemamigrate import migrate
        self.migrate = migrate
        migrate.reset()

    def test_upgrade_reports_steps_checksums_and_rollback(self):
        migrate = self.migrate
        migrate.insert({"name": "Ada Lovelace", "contact": "ada@example.com"})
        result = migrate.upgrade(3, operator="tester")
        self.assertTrue(result["ok"])
        self.assertEqual(migrate.current(), 3)
        self.assertEqual(len(result["steps"]), 2)
        for step in result["steps"]:
            self.assertEqual(step["rows_before"], step["rows_after"])
            self.assertEqual(len(step["checksum_before"]), 64)
            self.assertEqual(len(step["checksum_after"]), 64)
            self.assertNotEqual(step["checksum_before"], step["checksum_after"])
        self.assertEqual(result["rollback_point"], 1)
        self.assertEqual(result["operator"], "tester")
        self.assertGreaterEqual(result["duration"], 0)

    def test_downgrade_reverses_upgrade(self):
        migrate = self.migrate
        migrate.insert({"name": "Ada Lovelace", "contact": "ada@example.com"})
        before = migrate.rows()
        up = migrate.upgrade(3)
        down = migrate.downgrade(1)
        self.assertTrue(down["ok"])
        self.assertEqual(migrate.current(), 1)
        self.assertEqual(migrate.rows(), before)
        self.assertEqual(
            down["steps"][-1]["checksum_after"], up["steps"][0]["checksum_before"]
        )
        self.assertNotIn("email", migrate.rows()[0])
        self.assertNotIn("first_name", migrate.rows()[0])

    def test_failure_stops_and_preserves_scene(self):
        migrate = self.migrate
        migrate.insert({"name": "Ada Lovelace", "contact": "ada@example.com"})

        def boom(rows):
            raise RuntimeError("boom")

        bad = migrate.Step(4, "bad step", boom, lambda rows: None)
        migrate.STEPS.append(bad)
        try:
            result = migrate.upgrade(4)
        finally:
            migrate.STEPS.remove(bad)
        self.assertFalse(result["ok"])
        self.assertEqual(migrate.current(), 3)
        self.assertEqual(result["rollback_point"], 3)
        self.assertEqual(result["failed_step"], {"from": 3, "to": 4})
        self.assertIn("boom", result["error"])
        self.assertIn("email", migrate.rows()[0])

    def test_row_count_validation_failure(self):
        migrate = self.migrate
        migrate.insert({"name": "Ada Lovelace", "contact": "ada@example.com"})

        def drop_row(rows):
            rows.pop()

        bad = migrate.Step(4, "drops rows", drop_row, lambda rows: None)
        migrate.STEPS.append(bad)
        try:
            result = migrate.upgrade(4)
        finally:
            migrate.STEPS.remove(bad)
        self.assertFalse(result["ok"])
        self.assertEqual(migrate.current(), 3)
        self.assertIn("row count", result["error"])
        self.assertEqual(len(migrate.rows()), 0)

    def test_dual_write_and_compat_read(self):
        migrate = self.migrate
        migrate.upgrade(2)
        migrate.insert({"name": "Ada", "contact": "ada@example.com"})
        migrate.insert({"name": "Bob", "email": "bob@example.com"})
        row_a, row_b = migrate.rows()
        self.assertEqual(row_a["email"], "ada@example.com")
        self.assertEqual(row_b["contact"], "bob@example.com")
        legacy = {"name": "Cid", "contact": "cid@example.com"}
        self.assertEqual(migrate.read_field(legacy, "email"), "cid@example.com")
        self.assertEqual(migrate.read_field({"email": "d@e.com"}, "contact"), "d@e.com")

    def test_migration_log_records_operator_and_duration(self):
        migrate = self.migrate
        migrate.upgrade(2, operator="alice")
        migrate.downgrade(1, operator="bob")
        log = migrate.migration_log()
        self.assertEqual([r["action"] for r in log], ["upgrade", "downgrade"])
        self.assertEqual(log[0]["operator"], "alice")
        self.assertEqual(log[1]["operator"], "bob")
        for record in log:
            self.assertIn("version", record)
            self.assertGreaterEqual(record["duration"], 0)
