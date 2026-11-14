import unittest

from sitepolicy import DEFAULT, resolve, update
from sitepolicy import policy


class PolicyTest(unittest.TestCase):
    def test_site_overrides_default(self):
        self.assertEqual(resolve("shop", {"rate": 5})["rate"], 5)

    def test_update_is_global(self):
        update({"rate": 2})
        self.assertEqual(resolve("news")["rate"], 2)

    def test_no_audit(self):
        self.assertNotIn("history", update({"retry": 1}))


class LayeredPolicyTest(unittest.TestCase):
    def setUp(self):
        policy._reset()

    def test_three_layer_inheritance(self):
        update({
            "groups": {"news": {"rate": 3, "timeout": 10}},
            "sites": {"shop": {"group": "news", "rate": 5}},
        }, operator="alice")
        policy = resolve("shop")
        self.assertEqual(policy["rate"], 5)      # 站点层胜出
        self.assertEqual(policy["timeout"], 10)  # 继承分组层
        self.assertEqual(policy["retry"], 3)     # 继承默认层

    def test_conflict_resolution(self):
        update({
            "groups": {"news": {"rate": 3}},
            "sites": {"shop": {"group": "news", "rate": 5}},
        }, operator="alice")
        conflicts = {c["field"]: c for c in resolve("shop").conflicts}
        self.assertIn("rate", conflicts)
        self.assertEqual(conflicts["rate"]["winner"], "site")
        self.assertEqual(conflicts["rate"]["value"], 5)
        self.assertEqual(conflicts["rate"]["sources"],
                         {"default": 1, "group:news": 3, "site": 5})

    def test_failed_update_rolls_back(self):
        update({"rate": 2}, operator="alice")
        with self.assertRaises(ValueError):
            update({"rate": -1, "retry": 9}, operator="bad-op")
        resolved = resolve("news")
        self.assertEqual(resolved["rate"], 2)   # 旧快照保留
        self.assertEqual(resolved["retry"], 3)  # 失败 patch 不生效
        rejected = [e for e in policy.audit() if e["status"] == "rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["operator"], "bad-op")

    def test_audit_records_operator_and_changes(self):
        update({"rate": 2}, operator="alice")
        entry = policy.audit()[-1]
        self.assertEqual(entry["status"], "applied")
        self.assertEqual(entry["operator"], "alice")
        self.assertEqual(entry["version"], 1)
        self.assertIn({"layer": "default", "field": "rate",
                       "before": 1, "after": 2}, entry["changes"])
