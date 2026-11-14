import json
import os
import tempfile
import unittest

from schemadrift import (
    build_alert,
    check,
    diff,
    filter_report,
    list_baselines,
    load_baseline,
    save_baseline,
    should_alert,
    snapshot,
)
from schemadrift.store import archive_report


def table(name="users", env="prod", columns=None):
    return {
        "name": name,
        "env": env,
        "columns": columns if columns is not None else [
            {"name": "id", "type": "bigint", "nullable": False},
            {"name": "note", "type": "text", "nullable": True, "default": ""},
        ],
    }


class FourKindsTest(unittest.TestCase):
    """四类差异：新增、删除、类型、默认值。"""

    def test_added_non_null_without_default_is_high(self):
        base = table(columns=[{"name": "id", "type": "bigint", "nullable": False}])
        target = table(columns=[
            {"name": "id", "type": "bigint", "nullable": False},
            {"name": "amount", "type": "numeric", "nullable": False},
        ])
        report = diff(snapshot(base), snapshot(target))
        self.assertEqual(report["added"], ["amount"])
        self.assertEqual(report["summary"]["added"], 1)
        change = report["changes"][0]
        self.assertEqual(change["kind"], "added")
        self.assertEqual(change["level"], "high")
        self.assertIn("写入", change["suggestion"])

    def test_added_nullable_is_low(self):
        base = table(columns=[{"name": "id", "type": "bigint", "nullable": False}])
        target = table(columns=[
            {"name": "id", "type": "bigint", "nullable": False},
            {"name": "note", "type": "text", "nullable": True},
        ])
        report = diff(snapshot(base), snapshot(target))
        self.assertEqual(report["changes"][0]["level"], "low")

    def test_removed_is_high(self):
        base = table(columns=[
            {"name": "id", "type": "bigint", "nullable": False},
            {"name": "legacy", "type": "text", "nullable": True},
        ])
        target = table(columns=[{"name": "id", "type": "bigint", "nullable": False}])
        report = diff(snapshot(base), snapshot(target))
        self.assertEqual(report["removed"], ["legacy"])
        self.assertEqual(report["summary"]["removed"], 1)
        self.assertEqual(report["changes"][0]["kind"], "removed")
        self.assertEqual(report["changes"][0]["level"], "high")

    def test_type_change_is_high(self):
        base = table(columns=[{"name": "id", "type": "bigint", "nullable": False}])
        target = table(columns=[{"name": "id", "type": "int", "nullable": False}])
        report = diff(snapshot(base), snapshot(target))
        self.assertEqual(report["summary"]["type"], 1)
        change = report["changes"][0]
        self.assertEqual(change["kind"], "type")
        self.assertEqual(change["attribute"], "type")
        self.assertEqual(change["before"], "bigint")
        self.assertEqual(change["after"], "int")
        self.assertEqual(change["level"], "high")

    def test_nullable_relaxed_is_medium(self):
        base = table(columns=[{"name": "id", "type": "bigint", "nullable": False}])
        target = table(columns=[{"name": "id", "type": "bigint", "nullable": True}])
        report = diff(snapshot(base), snapshot(target))
        change = report["changes"][0]
        self.assertEqual(change["kind"], "type")
        self.assertEqual(change["attribute"], "nullable")
        self.assertEqual(change["level"], "medium")

    def test_default_change_is_medium_and_shows_values(self):
        base = table(columns=[{"name": "note", "type": "text", "nullable": True, "default": ""}])
        target = table(columns=[{"name": "note", "type": "text", "nullable": True, "default": "n/a"}])
        report = diff(snapshot(base), snapshot(target))
        change = report["changes"][0]
        self.assertEqual(change["kind"], "default")
        self.assertEqual(change["level"], "medium")
        self.assertEqual(change["before"], "")
        self.assertEqual(change["after"], "n/a")

    def test_default_removed_on_not_null_is_high(self):
        base = table(columns=[{"name": "state", "type": "text", "nullable": False, "default": "new"}])
        target = table(columns=[{"name": "state", "type": "text", "nullable": False}])
        report = diff(snapshot(base), snapshot(target))
        change = report["changes"][0]
        self.assertEqual(change["kind"], "default")
        self.assertEqual(change["level"], "high")

    def test_four_kinds_together(self):
        base = table(columns=[
            {"name": "id", "type": "bigint", "nullable": False},
            {"name": "old", "type": "text", "nullable": True},
            {"name": "status", "type": "smallint", "nullable": False, "default": 0},
        ])
        target = table(columns=[
            {"name": "id", "type": "bigint", "nullable": False},
            {"name": "status", "type": "bigint", "nullable": False, "default": 1},
            {"name": "new", "type": "text", "nullable": False},
        ])
        report = diff(snapshot(base), snapshot(target))
        self.assertEqual(report["summary"], {"added": 1, "removed": 1, "type": 1, "default": 1})
        self.assertTrue(report["has_drift"])
        self.assertEqual(report["level"], "high")
        self.assertTrue(report["fingerprint_changed"])


class FingerprintTest(unittest.TestCase):
    def test_fingerprint_collects_all_attributes(self):
        snap = snapshot(table(columns=[
            {"name": "a", "type": "text", "nullable": True, "default": "x"},
        ]))
        spec = snap["column_specs"]["a"]
        self.assertEqual(spec["type"], "text")
        self.assertTrue(spec["nullable"])
        self.assertTrue(spec["has_default"])
        self.assertTrue(spec["default_hash"].startswith("sha256:"))

    def test_fingerprint_stable_and_order_independent(self):
        left = snapshot({"columns": ["b", "a"]})
        right = snapshot({"columns": ["a", "b"]})
        self.assertEqual(left["fingerprint"], right["fingerprint"])

    def test_fingerprint_changes_with_type(self):
        a = snapshot({"columns": [{"name": "x", "type": "int"}]})
        b = snapshot({"columns": [{"name": "x", "type": "bigint"}]})
        self.assertNotEqual(a["fingerprint"], b["fingerprint"])

    def test_snapshot_is_idempotent(self):
        snap = snapshot({"columns": [{"name": "x", "type": "int"}]})
        self.assertEqual(snapshot(snap), snap)


class SensitiveTest(unittest.TestCase):
    """敏感字段只比哈希。"""

    def test_sensitive_default_plaintext_not_in_snapshot(self):
        snap = snapshot({"columns": [
            {"name": "api_token", "type": "text", "default": "sk-real-secret"},
        ]})
        spec = snap["column_specs"]["api_token"]
        self.assertTrue(spec["sensitive"])
        self.assertIsNone(spec["default"])
        self.assertTrue(spec["default_hash"])
        self.assertNotIn("sk-real-secret", json.dumps(snap))

    def test_explicit_sensitive_flag(self):
        snap = snapshot({"columns": [
            {"name": "opaque", "sensitive": True, "type": "text", "default": "hush"},
        ]})
        self.assertTrue(snap["column_specs"]["opaque"]["sensitive"])
        self.assertNotIn("hush", json.dumps(snap))

    def test_sensitive_default_diff_compares_hash_only(self):
        base = snapshot({"columns": [
            {"name": "password", "type": "text", "default": "old-pw"},
        ]})
        target = snapshot({"columns": [
            {"name": "password", "type": "text", "default": "new-pw"},
        ]})
        report = diff(base, target)
        change = report["changes"][0]
        self.assertEqual(change["kind"], "default")
        self.assertTrue(change["sensitive"])
        self.assertTrue(change["redacted"])
        self.assertTrue(change["before"].startswith("sha256:"))
        self.assertTrue(change["after"].startswith("sha256:"))
        self.assertNotIn("old-pw", json.dumps(report))
        self.assertNotIn("new-pw", json.dumps(report))

    def test_sensitive_same_value_no_diff(self):
        base = snapshot({"columns": [{"name": "secret", "type": "text", "default": "s"}]})
        target = snapshot({"columns": [{"name": "secret", "type": "text", "default": "s"}]})
        self.assertFalse(diff(base, target)["has_drift"])


class MultiEnvTest(unittest.TestCase):
    """多环境：指纹按环境分别保存，基线互不干扰。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_baseline_saved_per_env(self):
        prod = table(env="prod")
        staging = table(env="staging", columns=[
            {"name": "id", "type": "bigint", "nullable": False},
            {"name": "note", "type": "text", "nullable": True, "default": ""},
            {"name": "flagged", "type": "boolean", "nullable": True},
        ])
        save_baseline(prod, root=self.root)
        save_baseline(staging, root=self.root)

        prod_base = load_baseline("users", "prod", root=self.root)
        staging_base = load_baseline("users", "staging", root=self.root)
        self.assertEqual(prod_base["env"], "prod")
        self.assertEqual(staging_base["env"], "staging")
        self.assertNotEqual(prod_base["fingerprint"], staging_base["fingerprint"])
        self.assertEqual(prod_base["table"], "users")

        items = list_baselines(root=self.root)
        self.assertEqual(sorted(item["env"] for item in items), ["prod", "staging"])

    def test_check_uses_env_baseline(self):
        save_baseline(table(env="prod"), root=self.root)
        # prod 线上多了一列
        live_prod = table(env="prod", columns=[
            {"name": "id", "type": "bigint", "nullable": False},
            {"name": "note", "type": "text", "nullable": True, "default": ""},
            {"name": "hand_added", "type": "text", "nullable": True},
        ])
        report = check(live_prod, root=self.root)
        self.assertEqual(report["env"], "prod")
        self.assertTrue(report["has_drift"])
        self.assertEqual(report["added"], ["hand_added"])

        # staging 无基线，不应把 prod 的基线拿来比
        report = check(table(env="staging"), root=self.root)
        self.assertTrue(report["baseline_missing"])
        self.assertFalse(report["has_drift"])

    def test_check_without_drift(self):
        save_baseline(table(env="prod"), root=self.root)
        report = check(table(env="prod"), root=self.root)
        self.assertFalse(report["has_drift"])
        self.assertIsNone(report["level"])

    def test_env_not_part_of_fingerprint(self):
        self.assertEqual(
            snapshot(table(env="prod"))["fingerprint"],
            snapshot(table(env="staging"))["fingerprint"],
        )

    def test_report_archived_per_env(self):
        save_baseline(table(env="prod"), root=self.root)
        live = table(env="prod", columns=[
            {"name": "id", "type": "bigint", "nullable": False},
            {"name": "note", "type": "text", "nullable": True, "default": "n/a"},
        ])
        check(live, root=self.root)
        check(live, root=self.root)
        path = os.path.join(self.root, "prod", "audit.jsonl")
        with open(path, encoding="utf-8") as stream:
            records = [json.loads(line) for line in stream if line.strip()]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["env"], "prod")
        self.assertEqual(records[0]["table"], "users")
        self.assertFalse(os.path.exists(os.path.join(self.root, "staging")))


class FilterTest(unittest.TestCase):
    """差异报告可按字段过滤。"""

    def setUp(self):
        base = table(columns=[
            {"name": "id", "type": "bigint", "nullable": False},
            {"name": "note", "type": "text", "nullable": True, "default": ""},
        ])
        target = table(columns=[
            {"name": "id", "type": "int", "nullable": False},
            {"name": "note", "type": "text", "nullable": True, "default": "n/a"},
            {"name": "extra", "type": "text", "nullable": False},
        ])
        self.report = diff(snapshot(base), snapshot(target))

    def test_filter_by_column(self):
        base_cols = [{"name": "id", "type": "bigint", "nullable": False},
                     {"name": "note", "type": "text", "nullable": True, "default": ""}]
        target_cols = [{"name": "id", "type": "int", "nullable": False},
                       {"name": "note", "type": "text", "nullable": True, "default": "n/a"},
                       {"name": "extra", "type": "text", "nullable": False}]
        report = diff(
            snapshot(table(columns=base_cols)),
            snapshot(table(columns=target_cols)),
            columns=["note"],
        )
        self.assertEqual([c["column"] for c in report["changes"]], ["note"])
        self.assertEqual(report["added"], [])
        self.assertEqual(report["summary"]["default"], 1)
        self.assertEqual(report["summary"]["type"], 0)

    def test_filter_report_by_column(self):
        filtered = filter_report(self.report, columns="extra")
        self.assertEqual([c["column"] for c in filtered["changes"]], ["extra"])
        self.assertEqual(filtered["level"], "high")
        self.assertEqual(self.report["summary"], {"added": 1, "removed": 0, "type": 1, "default": 1})

    def test_filter_by_kind_and_level(self):
        filtered = filter_report(self.report, kinds=["default"])
        self.assertEqual(filtered["summary"]["default"], 1)
        self.assertEqual(filtered["level"], "medium")

        high_only = filter_report(self.report, min_level="high")
        self.assertTrue(high_only["changes"])
        self.assertTrue(all(c["level"] == "high" for c in high_only["changes"]))

    def test_diff_filters_compose(self):
        border = [{"name": "a", "type": "int", "default": 1}]
        nafter = [{"name": "a", "type": "int", "default": 2},
                  {"name": "b", "type": "text", "nullable": False}]
        report = diff(
            snapshot(table(columns=border)),
            snapshot(table(columns=nafter)),
            kinds=["default"],
        )
        self.assertEqual(report["summary"]["added"], 0)
        self.assertEqual(report["summary"]["default"], 1)


class AlertTest(unittest.TestCase):
    """告警按环境区分。"""

    def setUp(self):
        self.low_report = diff(
            snapshot(table(columns=[{"name": "id", "nullable": False}])),
            snapshot(table(columns=[{"name": "id", "nullable": False},
                                    {"name": "memo", "nullable": True}])),
        )
        self.low_report["target_env"] = "prod"

    def test_low_change_does_not_alert_prod(self):
        self.assertFalse(should_alert(self.low_report))
        self.assertIsNone(build_alert(self.low_report))

    def test_low_change_alerts_dev(self):
        dev = dict(self.low_report, target_env="dev")
        alert = build_alert(dev)
        self.assertIsNotNone(alert)
        self.assertEqual(alert["env"], "dev")
        self.assertEqual(alert["min_level"], "low")
        self.assertIn("[dev]", alert["message"])

    def test_high_change_alerts_prod(self):
        report = diff(
            snapshot(table(columns=[{"name": "id", "type": "bigint", "nullable": False},
                                    {"name": "gone", "type": "text", "nullable": True}])),
            snapshot(table(columns=[{"name": "id", "type": "bigint", "nullable": False}])),
        )
        report["target_env"] = "prod"
        self.assertTrue(should_alert(report))
        alert = build_alert(report)
        self.assertEqual(alert["env"], "prod")
        self.assertIn("[prod]", alert["message"])

    def test_environment_policies_are_independent(self):
        policies = {
            "prod": {"min_level": "high"},
            "dev": {"min_level": "low"},
            "default": {"min_level": "medium"},
        }
        self.assertFalse(should_alert(self.low_report, policies))
        self.assertTrue(should_alert(dict(self.low_report, target_env="dev"), policies))

    def test_sensitive_alert_has_no_plaintext(self):
        base = snapshot(table(columns=[{"name": "token", "type": "text", "default": "abc"}]))
        target = snapshot(table(columns=[{"name": "token", "type": "text", "default": "xyz"}]))
        report = diff(base, target)
        report["target_env"] = "prod"
        alert = build_alert(report)
        self.assertIsNotNone(alert)
        self.assertNotIn("abc", alert["message"])
        self.assertNotIn("xyz", alert["message"])


class LegacyCompatTest(unittest.TestCase):
    """旧的列名字符串写法仍然可用。"""

    def test_string_columns_snapshot(self):
        self.assertEqual(snapshot({"columns": ["b", "a"]})["columns"], ["a", "b"])

    def test_legacy_snapshot_diff_no_false_type_drift(self):
        old_base = {"table": "t", "columns": ["id"]}
        old_target = {"table": "t", "columns": ["id", "note"]}
        report = diff(old_base, old_target)
        self.assertEqual(report["added"], ["note"])
        self.assertEqual(report["summary"]["type"], 0)
        self.assertEqual(report["summary"]["default"], 0)


if __name__ == "__main__":
    unittest.main()
