import unittest

from metastore import Registry


class MetastoreTest(unittest.TestCase):
    def test_register(self):
        registry = Registry()
        self.assertEqual(registry.register({"name": "orders", "fields": ["id"]}), ["id"])

    def test_diff(self):
        registry = Registry()
        registry.register({"name": "orders", "fields": ["id"]})
        self.assertEqual(registry.diff({"name": "orders", "fields": ["id", "amt"]})["added"], ["amt"])
