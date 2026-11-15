import json
import os
import tempfile
import unittest

from datacontract2 import ContractHistory, check, export, matrix


class ContractTest(unittest.TestCase):
    def test_missing_field_is_breaking(self):
        old = {"fields": {"id": "int", "name": "text"}}
        new = {"fields": {"id": "int"}}
        self.assertEqual(check(old, new)["breaking"], ["name"])

    def test_type_change_is_ignored(self):
        old = {"fields": {"id": "int"}}
        new = {"fields": {"id": "text"}}
        self.assertTrue(check(old, new)["compatible"])

    def test_direction_is_read(self):
        self.assertEqual(check({}, {})["direction"], "read")


class DirectionTest(unittest.TestCase):
    """双向：读 / 写方向分别给结论。"""

    def test_type_widening_breaks_write_not_read(self):
        old = {"fields": {"id": "int"}}
        new = {"fields": {"id": "text"}}
        result = check(old, new)
        self.assertTrue(result["read"]["compatible"])
        self.assertFalse(result["write"]["compatible"])
        self.assertEqual(result["write"]["breaking"], ["id"])

    def test_field_removed_breaks_both_directions(self):
        old = {"fields": {"id": "int", "name": "text"}}
        new = {"fields": {"id": "int"}}
        result = check(old, new)
        self.assertIn("name", result["read"]["breaking"])
        self.assertIn("name", result["write"]["breaking"])

    def test_each_change_has_direction_field_and_suggestion(self):
        old = {"fields": {"id": "int", "name": "text"}}
        new = {"fields": {"id": "text"}}
        result = check(old, new)
        self.assertTrue(result["changes"])
        for change in result["changes"]:
            self.assertIn(change["direction"], ("read", "write"))
            self.assertTrue(change["field"])
            self.assertTrue(change["path"])
            self.assertTrue(change["suggestion"])


class TypeNarrowingTest(unittest.TestCase):
    def test_type_narrowing_breaks_read(self):
        old = {"fields": {"id": "text"}}
        new = {"fields": {"id": "int"}}
        result = check(old, new)
        self.assertFalse(result["compatible"])
        self.assertEqual(result["breaking"], ["id"])
        kinds = {c["kind"] for c in result["read"]["changes"]}
        self.assertIn("type_narrowed", kinds)

    def test_type_narrowing_ok_for_write(self):
        old = {"fields": {"id": "text"}}
        new = {"fields": {"id": "int"}}
        result = check(old, new)
        self.assertTrue(result["write"]["compatible"])

    def test_dict_style_field_declarations(self):
        old = {"fields": {"id": {"type": "text"}}}
        new = {"fields": {"id": {"type": "int"}}}
        self.assertFalse(check(old, new)["read"]["compatible"])


class DefaultTest(unittest.TestCase):
    """默认值变化区分新增和修改。"""

    def test_default_added_is_info(self):
        old = {"fields": {"status": "text"}}
        new = {"fields": {"status": {"type": "text", "default": "ok"}}}
        result = check(old, new)
        kinds = {c["kind"] for c in result["changes"]}
        self.assertIn("default_added", kinds)
        self.assertNotIn("default_changed", kinds)
        self.assertTrue(result["compatible"])

    def test_default_changed_is_warning(self):
        old = {"fields": {"status": {"type": "text", "default": "ok"}}}
        new = {"fields": {"status": {"type": "text", "default": "ng"}}}
        result = check(old, new)
        kinds = {c["kind"] for c in result["changes"]}
        self.assertIn("default_changed", kinds)
        self.assertNotIn("default_added", kinds)
        self.assertTrue(result["compatible"])
        self.assertTrue(result["warnings"])

    def test_default_removed_is_warning(self):
        old = {"fields": {"status": {"type": "text", "default": "ok"}}}
        new = {"fields": {"status": "text"}}
        result = check(old, new)
        kinds = {c["kind"] for c in result["changes"]}
        self.assertIn("default_removed", kinds)


class EnumTest(unittest.TestCase):
    def test_enum_value_removed_is_breaking(self):
        old = {"fields": {"state": {"type": "text", "enum": ["a", "b"]}}}
        new = {"fields": {"state": {"type": "text", "enum": ["a"]}}}
        result = check(old, new)
        self.assertFalse(result["compatible"])
        kinds = {c["kind"] for c in result["read"]["changes"]}
        self.assertIn("enum_removed", kinds)

    def test_enum_value_added_breaks_write(self):
        old = {"fields": {"state": {"type": "text", "enum": ["a"]}}}
        new = {"fields": {"state": {"type": "text", "enum": ["a", "b"]}}}
        result = check(old, new)
        self.assertTrue(result["read"]["compatible"])
        self.assertFalse(result["write"]["compatible"])
        kinds = {c["kind"] for c in result["write"]["changes"]}
        self.assertIn("enum_added", kinds)


class RequiredTest(unittest.TestCase):
    """必填变化双向判断。"""

    def test_optional_to_required_breaks_read(self):
        old = {"fields": {"name": {"type": "text"}}}
        new = {"fields": {"name": {"type": "text", "required": True}}}
        result = check(old, new)
        self.assertFalse(result["read"]["compatible"])
        self.assertTrue(result["write"]["compatible"])

    def test_required_to_optional_breaks_write(self):
        old = {"fields": {"name": {"type": "text", "required": True}}}
        new = {"fields": {"name": {"type": "text"}}}
        result = check(old, new)
        self.assertTrue(result["read"]["compatible"])
        self.assertFalse(result["write"]["compatible"])

    def test_new_required_field_without_default_breaks_read(self):
        old = {"fields": {"id": "int"}}
        new = {"fields": {"id": "int", "age": {"type": "int", "required": True}}}
        result = check(old, new)
        self.assertEqual(result["breaking"], ["age"])


class VersionTest(unittest.TestCase):
    def test_version_must_increase(self):
        old = {"version": 2, "fields": {}}
        new = {"version": 2, "fields": {}}
        result = check(old, new)
        self.assertFalse(result["version_ok"])
        kinds = {c["kind"] for c in result["changes"]}
        self.assertIn("version_not_monotonic", kinds)

    def test_version_increase_ok(self):
        old = {"version": 1, "fields": {}}
        new = {"version": 2, "fields": {}}
        self.assertTrue(check(old, new)["version_ok"])

    def test_no_version_skips_check(self):
        self.assertTrue(check({}, {})["version_ok"])


class OutputTest(unittest.TestCase):
    def test_export_writes_json_file(self):
        result = check({"fields": {"id": "int"}}, {"fields": {"id": "text"}})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.json")
            text = export(result, path)
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
        self.assertEqual(json.loads(text)["direction"], "read")
        self.assertEqual(loaded["read"]["compatible"], result["read"]["compatible"])

    def test_matrix_is_a_table(self):
        old = {"fields": {"id": "int", "name": "text"}}
        new = {"fields": {"id": "text"}}
        table = matrix(old, new)
        lines = table.splitlines()
        self.assertIn("字段", lines[0])
        self.assertIn("读方向", lines[0])
        self.assertIn("写方向", lines[0])
        body = "\n".join(lines[2:])
        self.assertIn("id", body)
        self.assertIn("name", body)
        self.assertIn("破坏", body)

    def test_migration_script_for_breaking_changes(self):
        old = {"name": "users", "fields": {"id": "text", "nick": "text"}}
        new = {"name": "users", "fields": {"id": "int"}}
        result = check(old, new)
        script = "\n".join(result["migration_script"])
        self.assertIn("DROP COLUMN nick", script)
        self.assertIn("ALTER COLUMN id TYPE int", script)

    def test_no_breaking_no_migration(self):
        result = check({"fields": {"id": "int"}}, {"fields": {"id": "int"}})
        self.assertEqual(result["migration_script"], [])


class HistoryTest(unittest.TestCase):
    def _v(self, version, fields):
        return {"version": version, "fields": fields}

    def test_history_keeps_versions_and_compares(self):
        history = ContractHistory()
        history.add(self._v(1, {"id": "int", "name": "text"}))
        history.add(self._v(2, {"id": "int"}))
        self.assertEqual(len(history), 2)
        self.assertEqual(history.compare()["breaking"], ["name"])
        self.assertEqual(history.latest()["version"], 2)

    def test_history_rejects_non_monotonic_version(self):
        history = ContractHistory()
        history.add(self._v(2, {}))
        with self.assertRaises(ValueError):
            history.add(self._v(2, {}))

    def test_history_persists_to_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = ContractHistory(tmp)
            history.add(self._v(1, {"id": "int"}))
            history.add(self._v(2, {"id": "text"}))
            reloaded = ContractHistory(tmp)
        self.assertEqual(len(reloaded), 2)
        self.assertFalse(reloaded.compare()["write"]["compatible"])


if __name__ == "__main__":
    unittest.main()
