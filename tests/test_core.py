import unittest

from datacontract import Registry


class DatacontractTest(unittest.TestCase):
    def test_register(self):
        registry = Registry()
        self.assertEqual(registry.register({"name": "orders", "fields": ["id"]}), ["id"])

    def test_diff_added(self):
        registry = Registry()
        self.assertEqual(registry.diff(["id"], ["id", "amount"]), {"added": ["amount"], "removed": []})

    def test_diff_removed(self):
        registry = Registry()
        self.assertEqual(registry.diff(["id", "note"], ["id"]), {"added": [], "removed": ["note"]})
