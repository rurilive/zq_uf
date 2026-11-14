import json
import os
import tempfile
import unittest

from datacontract import check, load, save


class ContractTest(unittest.TestCase):
    def test_missing_field_incompatible(self):
        self.assertFalse(check({"fields": ["a"]}, {"fields": []})["compatible"])

    def test_type_change_ignored(self):
        old = {"fields": ["a"], "types": {"a": "str"}}
        new = {"fields": ["a"], "types": {"a": "int"}}
        self.assertTrue(check(old, new)["compatible"])

    def test_no_direction(self):
        self.assertNotIn("direction", check({"fields": ["a"]}, {"fields": ["a"]}))


class DirectionTest(unittest.TestCase):
    def test_optional_column_added_compatible(self):
        old = {"fields": ["id"], "types": {"id": "int"}}
        new = {"fields": ["id", "note"], "types": {"id": "int", "note": "str"}}
        result = check(old, new)
        self.assertTrue(result["compatible"])
        self.assertTrue(result["backward_compatible"])
        self.assertTrue(result["forward_compatible"])

    def test_removed_column_breaks_forward_only(self):
        result = check({"fields": ["id", "note"]}, {"fields": ["id"]})
        self.assertFalse(result["compatible"])
        self.assertFalse(result["forward_compatible"])
        self.assertTrue(result["backward_compatible"])
        self.assertEqual(result["breaking"][0]["direction"], "forward")
        self.assertEqual(result["breaking"][0]["field"], "note")

    def test_required_column_added_flagged(self):
        old = {"fields": ["id"]}
        new = {"fields": ["id", "note"], "required": ["note"]}
        result = check(old, new)
        self.assertTrue(result["compatible"])
        kinds = [i["kind"] for i in result["issues"]]
        self.assertIn("required_field_added", kinds)

    def test_repeat_check_deterministic(self):
        old = {"fields": ["a", "b"], "types": {"a": "float"}, "defaults": {"b": 1}}
        new = {"fields": ["a"], "types": {"a": "int"}, "defaults": {}}
        self.assertEqual(check(old, new), check(old, new))


class TypeTighteningTest(unittest.TestCase):
    def test_float_to_int_breaking(self):
        old = {"fields": ["amount"], "types": {"amount": "float"}}
        new = {"fields": ["amount"], "types": {"amount": "int"}}
        result = check(old, new)
        self.assertFalse(result["compatible"])
        self.assertFalse(result["backward_compatible"])
        self.assertTrue(result["forward_compatible"])
        self.assertEqual(result["breaking"][0]["kind"], "type_tightened")
        self.assertTrue(result["breaking"][0]["suggestion"])

    def test_int_to_float_widening_compatible(self):
        old = {"fields": ["amount"], "types": {"amount": "int"}}
        new = {"fields": ["amount"], "types": {"amount": "float"}}
        result = check(old, new)
        self.assertTrue(result["compatible"])
        self.assertEqual(result["issues"][0]["kind"], "type_widened")
        self.assertEqual(result["issues"][0]["severity"], "warning")


class DefaultChangeTest(unittest.TestCase):
    def test_default_change_flagged_not_breaking(self):
        old = {"fields": ["a"], "defaults": {"a": 0}}
        new = {"fields": ["a"], "defaults": {"a": 1}}
        result = check(old, new)
        self.assertTrue(result["compatible"])
        issue = result["issues"][0]
        self.assertEqual(issue["kind"], "default_changed")
        self.assertEqual(issue["severity"], "warning")
        self.assertEqual(issue["direction"], "backward")
        self.assertEqual(issue["field"], "a")
        self.assertTrue(issue["suggestion"])

    def test_default_removed_flagged(self):
        old = {"fields": ["a"], "defaults": {"a": 0}}
        new = {"fields": ["a"]}
        result = check(old, new)
        self.assertTrue(result["compatible"])
        self.assertEqual(result["issues"][0]["kind"], "default_changed")


class EnumShrinkTest(unittest.TestCase):
    def test_enum_shrink_flagged_not_breaking(self):
        old = {"fields": ["s"], "enums": {"s": ["a", "b", "c"]}}
        new = {"fields": ["s"], "enums": {"s": ["a", "b"]}}
        result = check(old, new)
        self.assertTrue(result["compatible"])
        issue = result["issues"][0]
        self.assertEqual(issue["kind"], "enum_shrunk")
        self.assertEqual(issue["direction"], "backward")
        self.assertIn("c", issue["message"])

    def test_enum_expand_compatible(self):
        old = {"fields": ["s"], "enums": {"s": ["a"]}}
        new = {"fields": ["s"], "enums": {"s": ["a", "b"]}}
        self.assertTrue(check(old, new)["compatible"])


class RequiredChangeTest(unittest.TestCase):
    def test_optional_to_required_flagged(self):
        old = {"fields": ["a"]}
        new = {"fields": ["a"], "required": ["a"]}
        result = check(old, new)
        self.assertTrue(result["compatible"])
        self.assertEqual(result["issues"][0]["kind"], "required_changed")
        self.assertEqual(result["issues"][0]["direction"], "backward")


class ArchiveTest(unittest.TestCase):
    def test_save_load_roundtrip(self):
        contract = {
            "version": 2,
            "fields": ["id", "amount"],
            "types": {"id": "int", "amount": "float"},
            "required": ["id"],
            "defaults": {"amount": 0.0},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "contract_v2.json")
            save(contract, path)
            self.assertEqual(load(path), contract)
            with open(path, encoding="utf-8") as fh:
                json.load(fh)  # 归档文件是合法 JSON

    def test_archive_then_check_consistent(self):
        old = {"version": 1, "fields": ["id", "note"]}
        new = {"version": 2, "fields": ["id"]}
        with tempfile.TemporaryDirectory() as tmp:
            save(old, os.path.join(tmp, "v1.json"))
            save(new, os.path.join(tmp, "v2.json"))
            reread_old = load(os.path.join(tmp, "v1.json"))
            reread_new = load(os.path.join(tmp, "v2.json"))
        self.assertEqual(check(old, new), check(reread_old, reread_new))


if __name__ == "__main__":
    unittest.main()

