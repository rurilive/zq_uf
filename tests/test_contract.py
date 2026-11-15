import unittest

from datacontract2 import check


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
