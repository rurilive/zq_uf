import unittest

from jsonschema2 import repair, validate


class SchemaTest(unittest.TestCase):
    def test_missing_field_fails(self):
        self.assertFalse(validate({"id": 1}, {"required": ["id", "title"]})["ok"])

    def test_type_not_checked(self):
        self.assertTrue(validate({"id": "x"}, {"required": ["id"]})["ok"])

    def test_repair_is_noop(self):
        self.assertFalse(repair({"id": 1})["repaired"])
