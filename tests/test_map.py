import json
import os
import tempfile
import unittest

import partitionmap
from partitionmap import migrate, route


class MapTest(unittest.TestCase):
    def test_route_path(self):
        self.assertEqual(route("orders", "dt=1"), "orders/dt=1")

    def test_migrate_zero(self):
        self.assertEqual(migrate({"from": "a", "to": "b"})["moved"], 0)

    def test_route_keeps_partition(self):
        self.assertIn("dt=1", route("orders", "dt=1"))


class RuleTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        partitionmap.configure(state_dir=self.dir)

    def tearDown(self):
        partitionmap.configure(state_dir=self.dir)

    def add(self, **rule):
        return partitionmap.add_rule(rule)

    def test_rule_routes_to_target(self):
        self.add(name="new-store", dataset="orders", start="2026-02-01",
                 target="storage2/{dataset}/{partition}", priority=10)
        r = route("orders", "dt=2026-03-01")
        self.assertEqual(r, "storage2/orders/dt=2026-03-01")
        self.assertEqual(r.rule["name"], "new-store")
        self.assertIsNone(r.conflict)
        # 规则外的老分区仍走默认路径
        self.assertEqual(route("orders", "dt=2026-01-01"), "orders/dt=2026-01-01")

    def test_rule_priority_wins(self):
        self.add(name="low", dataset="orders",
                 target="low/{dataset}/{partition}", priority=1)
        self.add(name="high", dataset="orders",
                 target="high/{dataset}/{partition}", priority=9)
        r = route("orders", "dt=2026-01-01")
        self.assertEqual(r, "high/orders/dt=2026-01-01")
        self.assertEqual(r.rule["name"], "high")

    def test_conflict_outputs_two_rules(self):
        self.add(name="low", dataset="orders",
                 target="low/{dataset}/{partition}", priority=1)
        self.add(name="high", dataset="orders",
                 target="high/{dataset}/{partition}", priority=9)
        conflict = route("orders", "dt=2026-01-01").conflict
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["winner"], "high")
        self.assertEqual(conflict["resolved_by"], "priority")
        self.assertEqual([r["name"] for r in conflict["rules"]], ["high", "low"])

    def test_same_priority_stable_by_name(self):
        self.add(name="b-rule", dataset="orders",
                 target="b/{dataset}/{partition}", priority=5)
        self.add(name="a-rule", dataset="orders",
                 target="a/{dataset}/{partition}", priority=5)
        r = route("orders", "dt=2026-01-01")
        self.assertEqual(r.rule["name"], "a-rule")
        self.assertEqual(r.conflict["resolved_by"], "name")

    def test_tag_match(self):
        self.add(name="hot", dataset="orders", tags={"tier": "hot"},
                 target="ssd/{dataset}/{partition}", priority=10)
        r = route("orders", "dt=2026-01-01", tags={"tier": "hot"})
        self.assertEqual(r, "ssd/orders/dt=2026-01-01")
        self.assertEqual(route("orders", "dt=2026-01-01"), "orders/dt=2026-01-01")

    def test_rules_persisted_to_disk(self):
        self.add(name="new-store", dataset="orders",
                 target="s2/{dataset}/{partition}", priority=10)
        with open(os.path.join(self.dir, "rules.json"), encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk[0]["name"], "new-store")
        # 换一个实例读同一目录，规则仍在
        partitionmap.configure(state_dir=self.dir)
        self.assertEqual(route("orders", "dt=1"), "s2/orders/dt=1")


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        partitionmap.configure(state_dir=self.dir)
        self.plan = {
            "dataset": "orders",
            "partitions": ["dt=2026-01-01", "dt=2026-01-02"],
            "to": "storage2",
        }

    def tearDown(self):
        partitionmap.configure(state_dir=self.dir)

    def test_prepare_generates_mapping_and_dual_read(self):
        result = migrate(dict(self.plan, action="prepare"))
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["moved"], 0)
        self.assertEqual(
            result["mapping"]["dt=2026-01-01"],
            {"from": "orders/dt=2026-01-01", "to": "storage2/orders/dt=2026-01-01"},
        )
        r = route("orders", "dt=2026-01-01")
        self.assertEqual(r, "storage2/orders/dt=2026-01-01")
        self.assertTrue(r.dual_read)
        self.assertEqual(r.old_path, "orders/dt=2026-01-01")
        # 映射外分区不受影响
        self.assertFalse(route("orders", "dt=2026-02-01").dual_read)

    def test_switch_stops_old_path(self):
        mid = migrate(dict(self.plan, action="prepare"))["id"]
        result = migrate({"action": "switch", "id": mid})
        self.assertEqual(result["status"], "switched")
        self.assertEqual(result["moved"], 2)
        r = route("orders", "dt=2026-01-01")
        self.assertEqual(r, "storage2/orders/dt=2026-01-01")
        self.assertFalse(r.dual_read)
        self.assertIsNone(r.old_path)

    def test_rollback_restores_old_path(self):
        mid = migrate(dict(self.plan, action="prepare"))["id"]
        migrate({"action": "switch", "id": mid})
        result = migrate({"action": "rollback", "id": mid})
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(result["moved"], 0)
        r = route("orders", "dt=2026-01-01")
        self.assertEqual(r, "orders/dt=2026-01-01")
        self.assertFalse(r.dual_read)

    def test_migration_records_persisted(self):
        mid = migrate(dict(self.plan, action="prepare"))["id"]
        migrate({"action": "switch", "id": mid})
        with open(os.path.join(self.dir, "migrations.json"), encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual([r["status"] for r in on_disk], ["prepared", "switched"])
        self.assertTrue(all(r["id"] == mid for r in on_disk))

    def test_switch_without_prepare_is_noop(self):
        result = migrate({"action": "switch", "dataset": "orders"})
        self.assertEqual(result["status"], "noop")
        self.assertEqual(result["moved"], 0)


if __name__ == "__main__":
    unittest.main()
