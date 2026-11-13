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


class DiffClassificationTest(unittest.TestCase):
    def setUp(self):
        self.registry = Registry(downstream={"orders": ["daily_report", "billing_etl"]})
        self.registry.register({"name": "orders", "fields": [
            {"name": "id", "type": "int", "null_rate": 0.0},
            {"name": "amount", "type": "int", "null_rate": 0.0},
            {"name": "note", "type": "str", "null_rate": 0.1},
        ]})

    def test_diff_classification(self):
        result = self.registry.diff({"name": "orders", "fields": [
            {"name": "id", "type": "int", "null_rate": 0.0},
            {"name": "amount", "type": "float", "null_rate": 0.0},
            {"name": "currency", "type": "str", "null_rate": 0.0},
        ]})
        self.assertEqual(result["added"], ["currency"])
        self.assertEqual(result["removed"], ["note"])
        self.assertEqual(result["type_changed"], [{"field": "amount", "from": "int", "to": "float"}])

    def test_affected_tasks(self):
        changed = self.registry.diff({"name": "orders", "fields": [{"name": "id", "type": "int"}]})
        self.assertEqual(changed["affected_tasks"], ["daily_report", "billing_etl"])
        same = self.registry.diff({"name": "orders", "fields": [
            {"name": "id", "type": "int"},
            {"name": "amount", "type": "int"},
            {"name": "note", "type": "str"},
        ]})
        self.assertEqual(same["affected_tasks"], [])

    def test_diff_from_rows(self):
        result = self.registry.diff({"name": "orders", "rows": [
            {"id": "1", "amount": "10.5", "note": "a"},
            {"id": "2", "amount": "3.5", "note": ""},
        ]})
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["type_changed"], [{"field": "amount", "from": "int", "to": "float"}])


class AliasTest(unittest.TestCase):
    def test_alias_normalized_on_register_and_diff(self):
        registry = Registry(aliases={"amt": "amount"})
        self.assertEqual(registry.register({"name": "orders", "fields": ["id", "amt"]}), ["id", "amount"])
        result = registry.diff({"name": "orders", "fields": ["id", "amount"]})
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])

    def test_dataset_name_alias(self):
        registry = Registry(aliases={"orders_v2": "orders"})
        registry.register({"name": "orders", "fields": ["id"]})
        result = registry.diff({"name": "orders_v2", "fields": ["id"]})
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])

    def test_conflict_keeps_source(self):
        registry = Registry(aliases={"amt": "amount"})
        fields = registry.register({"name": "orders", "fields": [
            {"name": "amt", "type": "str"},
            {"name": "amount", "type": "float"},
        ]})
        self.assertEqual(fields, ["amount"])
        self.assertEqual(registry.get("orders")["fields"]["amount"]["type"], "float")


class VersionHistoryTest(unittest.TestCase):
    def test_versions_accumulate(self):
        registry = Registry()
        registry.register({"name": "orders", "fields": ["id"]})
        registry.register({"name": "orders", "fields": ["id", "amount"]})
        self.assertEqual(registry.version("orders"), 2)
        self.assertEqual(len(registry.history("orders")), 2)

    def test_get_specific_version(self):
        registry = Registry()
        registry.register({"name": "orders", "fields": ["id"]})
        registry.register({"name": "orders", "fields": ["id", "amount"]})
        first = registry.get("orders", 1)
        self.assertEqual(first["version"], 1)
        self.assertEqual(list(first["fields"]), ["id"])
        self.assertEqual(list(registry.get("orders")["fields"]), ["id", "amount"])
        self.assertIsNone(registry.get("orders", 99))
        self.assertIsNone(registry.get("missing"))

    def test_version_record_has_timestamp(self):
        registry = Registry()
        registry.register({"name": "orders", "fields": ["id"]})
        self.assertIn("registered_at", registry.get("orders"))
