import unittest

from contractdiff import check


class ContractTest(unittest.TestCase):
    def test_missing_field_breaks(self):
        self.assertEqual(check({"fields": {"a": "int"}}, {"fields": {}})["breaking"], ["a"])

    def test_type_change_ignored(self):
        self.assertTrue(check({"fields": {"a": "int"}}, {"fields": {"a": "text"}})["compatible"])

    def test_direction_read(self):
        self.assertEqual(check({}, {})["direction"], "read")
