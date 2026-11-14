import unittest

from datacontract import check


class ContractTest(unittest.TestCase):
    def test_missing_field_incompatible(self):
        self.assertFalse(check({"fields": ["a"]}, {"fields": []})["compatible"])

    def test_type_change_ignored(self):
        old = {"fields": ["a"], "types": {"a": "str"}}
        new = {"fields": ["a"], "types": {"a": "int"}}
        self.assertTrue(check(old, new)["compatible"])

    def test_no_direction(self):
        self.assertNotIn("direction", check({"fields": ["a"]}, {"fields": ["a"]}))
