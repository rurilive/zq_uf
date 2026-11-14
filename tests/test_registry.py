import unittest

from schemaversion import (
    IncompatibleSchemaError,
    UnknownVersionError,
    audit,
    chain,
    register,
    report,
    resolve,
    rollback,
    rollback_points,
)
from schemaversion import version as module


class RegistryTest(unittest.TestCase):
    def setUp(self):
        module._current = {"version": 0, "fields": {}}

    def test_register_records_field_type_and_default(self):
        record = register(
            {
                "version": 1,
                "fields": {
                    "id": "int",
                    "note": {"type": "str", "default": ""},
                },
            }
        )
        self.assertEqual(record["version"], 1)
        self.assertEqual(record["fields"]["id"], "int")
        self.assertEqual(
            record["fields"]["note"], {"type": "str", "default": ""}
        )

    def test_version_must_increase_monotonically(self):
        register({"version": 1, "fields": {"id": "int"}})
        with self.assertRaises(ValueError):
            register({"version": 1, "fields": {"id": "int"}})
        with self.assertRaises(ValueError):
            register({"version": 0, "fields": {"id": "int"}})

    def test_forward_compatible_add_field_with_default(self):
        register({"version": 1, "fields": {"id": "int"}})
        record = register(
            {
                "version": 2,
                "fields": {
                    "id": "int",
                    "note": {"type": "str", "default": ""},
                },
            }
        )
        self.assertTrue(record["compatible"])
        self.assertFalse(record["breaking"])
        self.assertIsNone(record["approver"])

    def test_breaking_add_field_without_default_requires_flag(self):
        register({"version": 1, "fields": {"id": "int"}})
        with self.assertRaises(IncompatibleSchemaError):
            register({"version": 2, "fields": {"id": "int", "note": "str"}})

    def test_breaking_remove_and_type_change_require_approval(self):
        register(
            {
                "version": 1,
                "fields": {
                    "id": "int",
                    "note": {"type": "str", "default": ""},
                },
            }
        )
        # 删字段：标记了破坏性但没有审批人
        with self.assertRaises(ValueError):
            register(
                {"version": 2, "fields": {"id": "int"}},
                breaking=True,
            )
        record = register(
            {"version": 2, "fields": {"id": "int"}},
            breaking=True,
            approver="alice",
        )
        self.assertTrue(record["breaking"])
        self.assertEqual(record["approver"], "alice")

        # 改类型：同样走破坏性审批
        with self.assertRaises(IncompatibleSchemaError):
            register({"version": 3, "fields": {"id": "str"}})
        record = register(
            {"version": 3, "fields": {"id": "str"}},
            breaking=True,
            approver="bob",
        )
        self.assertTrue(record["breaking"])
        self.assertEqual(record["approver"], "bob")

    def test_breaking_change_approver_must_be_non_empty(self):
        register({"version": 1, "fields": {"id": "int"}})
        with self.assertRaises(ValueError):
            register(
                {"version": 2, "fields": {"id": "int", "note": "str"}},
                breaking=True,
                approver="   ",
            )

    def test_resolve_by_version_and_rollback(self):
        register({"version": 1, "fields": {"id": "int"}})
        register(
            {
                "version": 2,
                "fields": {
                    "id": "int",
                    "note": {"type": "str", "default": ""},
                },
            }
        )
        self.assertEqual(resolve(1)["fields"], {"id": "int"})
        self.assertEqual(resolve()["version"], 2)

        rolled = rollback(1)
        self.assertEqual(rolled["version"], 1)
        self.assertEqual(resolve()["version"], 1)
        # 回滚后无默认值字段仍按类型字符串呈现（哨兵不得泄漏到对外视图）
        self.assertEqual(resolve()["fields"], {"id": "int"})
        # 回滚后旧版本链保留，两个版本都还能解析
        self.assertEqual(resolve(2)["fields"]["note"], {"type": "str", "default": ""})
        self.assertEqual(len(chain()), 2)
        self.assertEqual(rollback_points()[0], {**rollback_points()[0], "version": 1})

    def test_rollback_unknown_version(self):
        register({"version": 1, "fields": {"id": "int"}})
        with self.assertRaises(UnknownVersionError):
            rollback(9)
        with self.assertRaises(UnknownVersionError):
            resolve(9)

    def test_rollback_to_baseline_then_register_keeps_monotonic(self):
        register({"version": 1, "fields": {"id": "int"}})
        rollback(0)
        self.assertEqual(resolve()["version"], 0)
        with self.assertRaises(ValueError):
            register({"version": 1, "fields": {"id": "int"}})
        register({"version": 2, "fields": {"id": "int"}})
        self.assertEqual(resolve()["version"], 2)

    def test_audit_trail_covers_register_and_rollback(self):
        register({"version": 1, "fields": {"id": "int"}})
        register(
            {
                "version": 2,
                "fields": {"id": "int", "note": "str"},
            },
            breaking=True,
            approver="alice",
        )
        rollback(1)
        events = audit()
        self.assertEqual(
            [(event["event"], event.get("version") or event.get("to")) for event in events],
            [("register", 1), ("register", 2), ("rollback", 1)],
        )
        self.assertEqual(events[1]["approver"], "alice")
        self.assertTrue(events[1]["breaking"])
        self.assertEqual(events[2], {**events[2], "event": "rollback", "from": 2, "to": 1})

    def test_report_contains_chain_compatibility_and_rollback_points(self):
        register({"version": 1, "fields": {"id": "int"}})
        register(
            {
                "version": 2,
                "fields": {"id": "str"},
            },
            breaking=True,
            approver="alice",
        )
        rollback(1)
        text = report()
        self.assertIn("版本链", text)
        self.assertIn("v1", text)
        self.assertIn("兼容结论", text)
        self.assertIn("破坏性变更", text)
        self.assertIn("alice", text)
        self.assertIn("回滚点", text)


if __name__ == "__main__":
    unittest.main()
