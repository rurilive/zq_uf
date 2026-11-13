import unittest

from schemacheck import validate


class SchemacheckTest(unittest.TestCase):
    def test_required_passes(self):
        rows = [{"id": "1", "name": "a"}]
        result = validate(rows, {"required": ["id", "name"]})
        self.assertEqual(result["ok"], rows)
        self.assertEqual(result["bad"], [])

    def test_missing_field_rejected(self):
        rows = [{"id": "1"}, {"id": "2", "name": "b"}]
        result = validate(rows, {"required": ["id", "name"]})
        self.assertEqual(result["bad"][0]["row"], 0)
        self.assertEqual(result["bad"][0]["missing"], ["name"])

    def test_empty_rows(self):
        self.assertEqual(validate([], {"required": ["id"]}), {"ok": [], "bad": []})
