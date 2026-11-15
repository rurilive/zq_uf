import unittest

from jsonschema2 import repair, validate

SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer", "importance": "critical"},
        "title": {"type": "string", "default": "untitled", "importance": "important"},
        "status": {"type": "string", "enum": ["ok", "error"], "importance": "important"},
        "tags": {"type": "array", "items": {"type": "string"}, "importance": "optional"},
        "meta": {
            "type": "object",
            "importance": "optional",
            "properties": {
                "author": {"type": "string", "default": "anonymous", "importance": "important"},
                "score": {"type": "number", "importance": "optional"},
            },
        },
    },
    "required": ["id", "title"],
}


class PathValidationTest(unittest.TestCase):
    def test_type_error_reports_path(self):
        report = validate({"id": "x", "title": "t"}, SCHEMA)
        self.assertEqual(report["status"], "isolated")
        self.assertFalse(report["ok"])
        self.assertEqual(report["errors"][0]["path"], "$.id")
        self.assertEqual(report["errors"][0]["code"], "type")
        self.assertEqual(report["path"], "$.id")

    def test_enum_error_reports_path(self):
        report = validate({"id": 1, "title": "t", "status": "weird"}, SCHEMA)
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["errors"][0]["path"], "$.status")
        self.assertEqual(report["errors"][0]["code"], "enum")

    def test_nested_path_uses_dot_and_index(self):
        report = validate({"id": 1, "title": "t", "tags": ["a", 3]}, SCHEMA)
        self.assertEqual(report["errors"][0]["path"], "$.tags[1]")
        self.assertEqual(report["errors"][0]["code"], "type")

    def test_nested_object_path(self):
        report = validate({"id": 1, "title": "t", "meta": {"score": "high"}}, SCHEMA)
        self.assertEqual(report["errors"][0]["path"], "$.meta.score")

    def test_nested_critical_isolates(self):
        schema = {
            "type": "object",
            "properties": {
                "meta": {
                    "type": "object",
                    "properties": {"author": {"type": "string"}},
                    "required": ["author"],
                }
            },
            "required": ["meta"],
        }
        report = validate({"meta": {"author": 5}}, schema)
        self.assertEqual(report["status"], "isolated")
        self.assertEqual([item["path"] for item in report["isolated"]], ["$.meta.author"])


class DegradeTest(unittest.TestCase):
    def test_important_field_degraded_with_source(self):
        report = validate({"id": 1}, SCHEMA)
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["payload"]["title"], "untitled")
        self.assertEqual(
            report["provenance"], [{"path": "$.title", "source": "schema_default"}]
        )

    def test_enum_field_degrades_to_first_enum_value(self):
        report = validate({"id": 1, "title": "t", "status": "bad"}, SCHEMA)
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["payload"]["status"], "ok")
        action = report["degraded"][0]
        self.assertEqual(action["source"], "enum_first")
        self.assertEqual(action["path"], "$.status")

    def test_nested_important_child_replaced_not_isolated(self):
        report = validate({"id": 1, "title": "t", "meta": {"author": 5}}, SCHEMA)
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["payload"]["meta"]["author"], "anonymous")

    def test_critical_missing_isolates(self):
        payload = {"id": 1, "title": "t", "tags": ["a", 3]}
        report = validate(payload, SCHEMA)
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["payload"]["tags"], ["a"])
        self.assertEqual(report["degraded"][0]["action"], "drop")

    def test_important_child_replaces_and_keeps_record(self):
        report = validate({"id": 1, "title": "t", "meta": {"score": "high"}}, SCHEMA)
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["payload"]["meta"], {})

    def test_critical_missing_isolates(self):
        report = validate({"title": "t"}, SCHEMA)
        self.assertEqual(report["status"], "isolated")
        self.assertIsNone(report["payload"])
        self.assertEqual(report["isolated"][0]["path"], "$.id")

    def test_input_not_mutated(self):
        payload = {"id": 1, "title": "t", "status": "bad"}
        validate(payload, SCHEMA)
        self.assertEqual(payload["status"], "bad")


class RepairTest(unittest.TestCase):
    def test_repair_uses_schema_and_marks_source(self):
        result = repair({"id": 1}, SCHEMA)
        self.assertTrue(result["repaired"])
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["payload"]["title"], "untitled")
        self.assertEqual(result["provenance"][0]["source"], "schema_default")

    def test_repair_isolated_returns_none_payload(self):
        result = repair({"id": "x", "title": "t"}, SCHEMA)
        self.assertEqual(result["status"], "isolated")
        self.assertIsNone(result["payload"])
        self.assertFalse(result["repaired"])

    def test_repair_without_schema_is_noop(self):
        result = repair({"id": 1})
        self.assertEqual(result["payload"], {"id": 1})
        self.assertFalse(result["repaired"])


if __name__ == "__main__":
    unittest.main()
