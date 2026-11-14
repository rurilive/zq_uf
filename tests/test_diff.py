import unittest

from schemadiff import diff, impact


class DiffTest(unittest.TestCase):
    def test_added_removed(self):
        result = diff({"fields": [{"name": "a"}]}, {"fields": [{"name": "b"}]})
        self.assertEqual(result["added"], ["b"])

    def test_type_change_ignored(self):
        old = {"fields": [{"name": "a", "type": "str"}]}
        new = {"fields": [{"name": "a", "type": "int"}]}
        self.assertEqual(diff(old, new)["added"], [])

    def test_impact_empty(self):
        self.assertEqual(impact({"added": ["a"]}), [])

    def test_type_change_marked(self):
        old = {"fields": [{"name": "a", "type": "str"}]}
        new = {"fields": [{"name": "a", "type": "int"}]}
        items = diff(old, new)["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "type_changed")
        self.assertEqual(items[0]["level"], "critical")

    def test_default_change(self):
        old = {"fields": [{"name": "a", "type": "int", "default": 0}]}
        new = {"fields": [{"name": "a", "type": "int", "default": 1}]}
        items = diff(old, new)["items"]
        self.assertEqual(items[0]["kind"], "default_changed")
        self.assertEqual(items[0]["level"], "minor")

    def test_default_removed(self):
        old = {"fields": [{"name": "a", "default": 0}]}
        new = {"fields": [{"name": "a"}]}
        items = diff(old, new)["items"]
        self.assertEqual(items[0]["kind"], "default_removed")
        self.assertEqual(items[0]["level"], "major")

    def test_pk_removed_is_critical(self):
        old = {"fields": [{"name": "id", "type": "int", "primary_key": True}]}
        new = {"fields": [{"name": "id", "type": "int"}]}
        items = diff(old, new)["items"]
        self.assertEqual(items[0]["kind"], "pk_removed")
        self.assertEqual(items[0]["level"], "critical")

    def test_items_sorted_by_priority(self):
        old = {"fields": [{"name": "z", "default": 0}, {"name": "a", "type": "str"}]}
        new = {"fields": [{"name": "z", "default": 1}, {"name": "a", "type": "int"}]}
        items = diff(old, new)["items"]
        self.assertEqual([i["kind"] for i in items], ["type_changed", "default_changed"])

    def test_impact_paths(self):
        old = {"name": "orders", "fields": [{"name": "id", "type": "int", "primary_key": True}]}
        new = {"name": "orders", "fields": [{"name": "id", "type": "str", "primary_key": True}]}
        lineage = {
            "orders.id": ["dm_orders.order_id"],
            "dm_orders.order_id": ["rpt_daily.order_id"],
        }
        result = impact(diff(old, new), lineage)
        self.assertEqual(len(result), 1)
        entry = result[0]
        self.assertEqual(entry["field"], "id")
        self.assertEqual(entry["affected_tables"], ["dm_orders", "rpt_daily"])
        self.assertIn(["orders.id", "dm_orders.order_id", "rpt_daily.order_id"], entry["paths"])

    def test_impact_no_lineage_hit(self):
        payload = diff({"fields": [{"name": "a"}]}, {"fields": []})
        self.assertEqual(impact(payload, {"other.x": ["y.z"]}), [])
