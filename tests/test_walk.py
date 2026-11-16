import unittest

from privpath import config_changed, record_reproduction, report, reset, walk


OPS = [
    {
        "op": "read-config",
        "needs": "user",
        "effect": "admin",
        "preconditions": ["config-portal-open"],
        "config": ["svc.config_portal"],
    },
    {"op": "dump-db", "needs": "admin", "effect": "root", "config": ["db.backup_job"]},
    {"op": "open-shell", "needs": "root", "effect": "host"},
    {"op": "sudo-reload", "needs": "admin", "effect": ""},
]


class WalkTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_account_required(self):
        with self.assertRaises(ValueError):
            walk({"role": "user"})

    def test_multi_hop_followed(self):
        got = walk({"account": "dev", "role": "user", "ops": OPS})
        self.assertEqual(got["reachable"], ["admin", "root", "host"])

    def test_paths_archived_by_target(self):
        got = walk({"account": "dev", "role": "user", "ops": OPS})
        self.assertEqual(sorted(got["by_target"]), ["admin", "host", "root"])
        host_path = got["by_target"]["host"][0]
        self.assertEqual(
            [s["op"] for s in host_path["steps"]],
            ["read-config", "dump-db", "open-shell"],
        )

    def test_each_step_records_prerequisite_account_and_effect(self):
        got = walk({"account": "dev", "role": "user", "ops": OPS})
        first = got["by_target"]["admin"][0]["steps"][0]
        self.assertEqual(first["account"], "dev")
        self.assertEqual(first["needs"], "user")
        self.assertEqual(first["effect"], "admin")
        self.assertEqual(first["preconditions"], ["config-portal-open"])
        self.assertEqual(first["config"], ["svc.config_portal"])

    def test_failed_precondition_blocks_branch_and_keeps_it(self):
        got = walk(
            {
                "account": "dev",
                "role": "user",
                "ops": OPS,
                "conditions": {"config-portal-open": False},
            }
        )
        self.assertEqual(got["reachable"], [])
        # read-config 前提不满足，下游三个操作因 admin/root 不可达一并留档
        self.assertEqual(len(got["blocked"]), 4)
        self.assertEqual(got["blocked"][0]["op"], "read-config")
        self.assertIn("precondition not satisfied", got["blocked"][0]["reason"])
        blocked_ops = {b["op"] for b in got["blocked"]}
        self.assertEqual(blocked_ops, {"read-config", "dump-db", "open-shell", "sudo-reload"})

    def test_unreachable_roles_kept_as_blocked(self):
        ops = [
            {"op": "read-config", "needs": "user", "effect": "admin"},
            {"op": "cluster-op", "needs": "cluster-admin", "effect": "cloud"},
        ]
        got = walk({"account": "qa", "role": "guest", "ops": ops})
        blocked = {b["op"]: b for b in got["blocked"]}
        self.assertEqual(blocked["cluster-op"]["status"], "blocked")
        self.assertIn("not reachable", blocked["cluster-op"]["reason"])

    def test_no_effect_op_executed_but_not_a_target(self):
        got = walk({"account": "qa2", "role": "user", "ops": OPS})
        self.assertIn("sudo-reload", {s["op"] for s in got["steps"]})
        self.assertNotIn("sudo-reload", got["by_target"])

    def test_steps_use_op_specific_account(self):
        ops = [
            {"op": "steal-token", "needs": "user", "effect": "svc", "account": "ci-bot"},
            {"op": "svc-action", "needs": "svc", "effect": "root", "account": "ci-bot"},
        ]
        got = walk({"account": "dev", "ops": ops})
        self.assertEqual(got["accounts"], ["ci-bot"])
        self.assertEqual(
            [s["account"] for s in got["by_target"]["root"][0]["steps"]],
            ["ci-bot", "ci-bot"],
        )

    def test_cycle_does_not_loop_forever(self):
        ops = [
            {"op": "a-to-b", "needs": "user", "effect": "admin"},
            {"op": "b-to-a", "needs": "admin", "effect": "user"},
        ]
        got = walk({"account": "looper", "ops": ops})
        self.assertEqual(got["reachable"], ["admin"])

    def test_unknown_precondition_does_not_block(self):
        got = walk({"account": "dev", "role": "user", "ops": OPS})
        self.assertIn("config-portal-open", got["preconditions"])
        self.assertTrue(
            any(
                s["op"] == "read-config"
                for path in got["paths"]
                for s in path["steps"]
            )
        )


class ConfigChangeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_changed_config_marks_affected_paths(self):
        walk({"account": "dev", "role": "user", "ops": OPS})
        result = config_changed(["db.backup_job"])
        self.assertEqual(len(result["affected"]), 1)
        self.assertEqual(result["affected"][0]["start"], "dev")
        self.assertEqual(result["affected"][0]["changed"], ["db.backup_job"])
        self.assertIn(
            {"start": "dev", "changed": ["db.backup_job"]},
            report()["affected"],
        )

    def test_unrelated_config_change_marks_nothing(self):
        walk({"account": "dev", "role": "user", "ops": OPS})
        result = config_changed(["unrelated.flag"])
        self.assertEqual(result["affected"], [])

    def test_affected_marks_accumulate_until_rewalk(self):
        walk({"account": "dev", "role": "user", "ops": OPS})
        config_changed(["db.backup_job"])
        config_changed(["svc.config_portal"])
        self.assertEqual(
            _record("dev")["affected"],
            ["db.backup_job", "svc.config_portal"],
        )
        walk({"account": "dev", "role": "user", "ops": OPS})
        self.assertEqual(_record("dev")["affected"], [])


class ReproductionTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_failed_reproduction_keeps_original_conclusion(self):
        got = walk({"account": "dev", "role": "user", "ops": OPS})
        self.assertIn("root", got["reachable"])
        record_reproduction(
            "dev", "dev-staging", "root",
            "staging 关闭了 db.backup_job，dump-db 不可用",
        )
        after = _record("dev")
        self.assertIn("root", after["reachable"])  # 原结论保留
        self.assertEqual(len(after["reproductions"]), 1)
        self.assertFalse(after["reproductions"][0]["ok"])
        self.assertEqual(
            report()["failures"][0]["reason"],
            "staging 关闭了 db.backup_job，dump-db 不可用",
        )

    def test_reproduction_against_unknown_target_rejected(self):
        walk({"account": "dev", "role": "user", "ops": OPS})
        with self.assertRaises(ValueError):
            record_reproduction("dev", "dev2", "host-cluster", "no such target")

    def test_reproduction_account_listed(self):
        walk({"account": "dev", "role": "user", "ops": OPS})
        record_reproduction("dev", "dev-staging", "root", "blocked")
        record_reproduction("dev", "dev-prod", "root", "blocked")
        # 复现账号与路径中用到的账号一并进入账号清单，方便回收
        self.assertEqual(report()["failures"][0]["account"], "dev-staging")
        self.assertIn("dev-staging", report()["accounts"])
        self.assertIn("dev-prod", report()["accounts"])


class ReportTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_report_archives_by_target_and_lists_accounts(self):
        ops = [
            {"op": "read-config", "needs": "user", "effect": "admin",
             "account": "svc-a", "config": ["svc.config_portal"]},
            {"op": "dump-db", "needs": "admin", "effect": "root",
             "account": "svc-b", "config": ["db.backup_job"]},
        ]
        walk({"account": "dev", "ops": ops})
        walk({"account": "ops", "role": "admin", "ops": ops})
        out = report()
        self.assertEqual(out["paths"], 3)  # dev->admin, dev->root, ops->root
        self.assertEqual(out["reachable"], ["admin", "root"])
        root_sources = {p["start"] for p in out["by_target"]["root"]}
        self.assertEqual(root_sources, {"dev", "ops"})
        self.assertEqual(out["accounts"], ["svc-a", "svc-b"])
        self.assertEqual(
            out["config"]["db.backup_job"], ["dev->root", "ops->root"]
        )

    def test_report_includes_blocked_branches(self):
        walk(
            {
                "account": "dev",
                "ops": OPS,
                "conditions": {"config-portal-open": False},
            }
        )
        out = report()
        self.assertEqual(out["blocked_count"], len(out["blocked"]))
        self.assertGreater(out["blocked_count"], 0)
        self.assertEqual(out["blocked"][0]["start"], "dev")

    def test_empty_report(self):
        out = report()
        self.assertEqual(out["paths"], 0)
        self.assertEqual(out["reachable"], [])
        self.assertEqual(out["accounts"], [])


def _record(account):
    # 通过重新 walk 之外的途径观察内部记录：直接从 report 无法按账号取，
    # 用 walk 后 report 的 starts 间接验证不够，这里借助模块内部存储。
    from privpath.walk import _PATHS
    return _PATHS[account]


if __name__ == "__main__":
    unittest.main()
