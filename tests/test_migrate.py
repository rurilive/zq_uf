import unittest

from schemamigrate import Migration, Migrator, current, downgrade, upgrade


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
        self.m = Migrator()
        self.m.tables["users"] = [
            {"id": 1, "name": "alice"},
            {"id": 2, "name": "bob"},
        ]

        def to_fullname(rows):
            return [{"id": r["id"], "fullname": r["name"]} for r in rows]

        def to_name(rows):
            return [{"id": r["id"], "name": r["fullname"]} for r in rows]

        self.m.register(Migration(2, to_fullname, to_name, table="users",
                                  aliases={"fullname": "name"}))

    def test_upgrade_runs_steps_with_checksums(self):
        result = self.m.upgrade(2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], 2)
        self.assertEqual(result["rollback_point"], 1)
        step = result["steps"][0]
        self.assertEqual(step["rows_before"], step["rows_after"])
        self.assertNotEqual(step["checksum_before"], step["checksum_after"])
        self.assertEqual(self.m.tables["users"][0]["fullname"], "alice")
        record = self.m.history[-1]
        self.assertEqual(record["version"], 2)
        self.assertTrue(record["operator"])
        self.assertGreaterEqual(record["duration_ms"], 0)

    def test_downgrade_reverses_steps(self):
        self.m.upgrade(2)
        result = self.m.downgrade(1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["rollback_point"], 2)
        self.assertEqual(self.m.tables["users"],
                         [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}])

    def test_failed_step_stops_and_preserves_scene(self):
        def bad_forward(rows):
            raise RuntimeError("boom")

        self.m.register(Migration(3, bad_forward, lambda rows: rows,
                                  table="users"))
        self.m.upgrade(2)
        result = self.m.upgrade(3)
        self.assertFalse(result["ok"])
        self.assertEqual(result["version"], 2)
        self.assertEqual(result["rollback_point"], 2)
        self.assertEqual(result["failure"]["step"], 3)
        self.assertIn("boom", result["failure"]["error"])
        self.assertEqual(result["failure"]["scene"]["rows"],
                         self.m.tables["users"])
        self.assertEqual(len(result["steps"]), 1)

    def test_row_count_mismatch_fails_step(self):
        self.m.register(Migration(3, lambda rows: rows[:-1], lambda rows: rows,
                                  table="users"))
        self.m.upgrade(2)
        result = self.m.upgrade(3)
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.m.tables["users"]), 2)

    def test_dual_write_and_compat_read(self):
        row = self.m.insert("users", {"id": 3, "fullname": "carol"})
        self.assertEqual(row["name"], "carol")
        row = self.m.insert("users", {"id": 4, "name": "dave"})
        self.assertEqual(row["fullname"], "dave")
        self.assertEqual(self.m.read_field({"name": "old"}, "fullname"), "old")
        self.assertEqual(self.m.read_field({"fullname": "new"}, "name"), "new")
