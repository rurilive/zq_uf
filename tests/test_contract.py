import unittest

from dqcontract import check, enforce


class ContractTest(unittest.TestCase):
    def test_missing_required_fails(self):
        result = check({"rows": [{"id": None}]}, {"required": ["id"]})
        self.assertFalse(result["ok"])

    def test_only_first_field_checked(self):
        result = check({"rows": [{"id": 1, "name": None}]}, {"required": ["id", "name"]})
        self.assertTrue(result["ok"])

    def test_enforce_does_not_block(self):
        self.assertFalse(enforce({"ok": False})["blocked"])
