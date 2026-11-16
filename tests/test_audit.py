import unittest

from permaudit import audit, grant, reclaim, recheck, report, changes, reset

ACCOUNT = {
    "id": "u100",
    "system": "billing",
    "role": "support",
    "status": "active",
    "permissions": ["invoice.read", "invoice.refund", "user.export"],
    "used": {"invoice.read": 12, "user.export": 3},
}


class AuditTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_unused_is_membership_based(self):
        self.assertEqual(audit(ACCOUNT)["unused"], ["invoice.refund"])

    def test_fresh_permission_is_flagged_unused(self):
        account = dict(ACCOUNT, id="u101", permissions=["report.export"], used={})
        account["granted_day"] = 100
        account["now"] = 101
        result = audit(account)
        # 新授权：从未使用仍算 unused，但不算长期未使用
        self.assertEqual(result["unused"], ["report.export"])
        self.assertEqual(result["stale"], [])

    def test_old_never_used_permission_is_stale(self):
        account = dict(ACCOUNT, id="u105", permissions=["report.export"], used={})
        account["granted_day"] = 1
        account["now"] = 60
        result = audit(account)
        self.assertEqual(result["stale"], ["report.export"])
        # 在职账号的长期未使用权限进入待收回清单
        self.assertEqual(
            [item["permission"] for item in result["reclaim"]],
            ["report.export"],
        )
        self.assertEqual(result["reclaim"][0]["reason"], "stale")

    def test_permission_unused_long_ago_is_stale(self):
        account = dict(
            ACCOUNT, id="u106",
            permissions=["invoice.read"],
            used={"invoice.read": 45},
        )
        self.assertEqual(audit(account)["stale"], ["invoice.read"])

    def test_left_account_is_abnormal_and_all_perms_reclaimable(self):
        account = dict(ACCOUNT, id="u102", status="left")
        result = audit(account)
        self.assertEqual(len(result["abnormal"]), 1)
        self.assertEqual(result["abnormal"][0]["status"], "left")
        self.assertEqual(
            sorted(item["permission"] for item in result["reclaim"]),
            sorted(ACCOUNT["permissions"]),
        )
        self.assertTrue(all(item["reason"] == "left" for item in result["reclaim"]))

    def test_transferred_account_is_abnormal(self):
        account = dict(ACCOUNT, id="u107", status="transferred")
        result = audit(account)
        self.assertEqual(result["abnormal"][0]["reason"], "transferred")
        self.assertEqual(len(result["reclaim"]), len(ACCOUNT["permissions"]))

    def test_report_counts_accounts_per_system(self):
        audit(dict(ACCOUNT, id="u103", system="audit-only"))
        body = report()
        self.assertEqual(body["systems"]["audit-only"], 1)
        # 该账号没有待收回项（权限近期都在用）
        self.assertEqual(body["reclaim_count"], 0)
        self.assertEqual(body["reclaim"], {})

    def test_report_reclaim_grouped_by_system_and_role(self):
        audit(dict(ACCOUNT, id="u110", status="left"))
        audit(dict(
            ACCOUNT, id="u111", system="billing", role="cashier", status="left",
            permissions=["invoice.read"], used={},
        ))
        body = report()
        self.assertIn("billing", body["reclaim"])
        self.assertIn("support", body["reclaim"]["billing"])
        self.assertIn("cashier", body["reclaim"]["billing"])
        support_perms = [
            item["permission"] for item in body["reclaim"]["billing"]["support"]
        ]
        self.assertIn("invoice.refund", support_perms)
        self.assertEqual(
            body["reclaim_count"],
            sum(len(v) for roles in body["reclaim"].values() for v in roles.values()),
        )

    def test_report_can_be_filtered_by_system(self):
        audit(dict(ACCOUNT, id="u120", system="billing", status="left"))
        audit(dict(ACCOUNT, id="u121", system="crm", status="left"))
        body = report(system="crm")
        self.assertEqual(body["accounts"], 1)
        self.assertEqual(list(body["systems"]), ["crm"])
        self.assertEqual(
            [item["account"] for item in body["abnormal"]], ["u121"]
        )

    def test_long_inactive_accounts_are_counted_separately(self):
        audit(dict(ACCOUNT, id="u130", last_login=90))
        audit(dict(ACCOUNT, id="u131", last_login=5))
        body = report()
        self.assertEqual(body["inactive"]["count"], 1)
        self.assertEqual(body["inactive"]["accounts"][0]["account"], "u130")
        self.assertIn("threshold", body["inactive"])

    def test_grant_records_actor_and_time(self):
        audit(dict(ACCOUNT, id="u104"))
        result = grant("u104", "audit.read", actor="alice")
        self.assertTrue(result["recorded"])
        self.assertEqual(result["permission"], "audit.read")
        self.assertEqual(result["actor"], "alice")
        self.assertIsNotNone(result["time"])

    def test_change_log_is_time_ordered_and_filterable(self):
        audit(dict(ACCOUNT, id="u140"))
        grant("u140", "a.read", actor="alice")
        grant("u140", "b.read", actor="bob")
        log = changes("u140")
        self.assertEqual([item["permission"] for item in log], ["a.read", "b.read"])
        self.assertEqual([item["seq"] for item in log], sorted(item["seq"] for item in log))
        self.assertTrue(all("time" in item and "actor" in item for item in log))
        self.assertEqual(changes("u999"), [])

    def test_grant_unknown_account_raises(self):
        with self.assertRaises(ValueError):
            grant("ghost", "a.read")

    def test_reclaim_dry_run_gives_impact_without_change(self):
        audit(dict(ACCOUNT, id="u150", status="left"))
        result = reclaim("u150", actor="alice", dry_run=True)
        self.assertFalse(result["applied"])
        self.assertEqual(
            result["impact"]["accounts_affected"], ["u150"]
        )
        self.assertEqual(
            result["impact"]["permission_count"], len(ACCOUNT["permissions"])
        )
        self.assertEqual(audit(dict(ACCOUNT, id="u150", status="left"))["granted"],
                         ACCOUNT["permissions"])

    def test_reclaim_removes_permissions_and_logs(self):
        audit(dict(ACCOUNT, id="u160", status="left"))
        result = reclaim("u160", permissions=["invoice.refund"], actor="alice")
        self.assertTrue(result["applied"])
        self.assertEqual(result["removed"], ["invoice.refund"])
        self.assertNotIn("invoice.refund", result["impact"]["remaining"])
        body = report()
        self.assertIn("u160", body["pending_recheck"])
        log = changes("u160")
        self.assertEqual(log[-1]["action"], "reclaim")
        self.assertEqual(log[-1]["actor"], "alice")

    def test_recheck_only_changed_accounts_and_once(self):
        account = dict(ACCOUNT, id="u170", status="left")
        audit(account)
        # 未发生变化的重复审计不产生待复核
        audit(dict(account))
        self.assertNotIn("u170", report()["pending_recheck"])

        reclaim("u170", permissions=["invoice.refund"], actor="alice")
        self.assertIn("u170", report()["pending_recheck"])

        first = recheck("u170")
        self.assertTrue(first["reviewed"])
        self.assertFalse(first["already_reviewed"])
        self.assertNotIn("u170", report()["pending_recheck"])

        second = recheck("u170")
        self.assertTrue(second["already_reviewed"])

    def test_new_change_after_recheck_reopens_review(self):
        account = dict(ACCOUNT, id="u180", status="left")
        audit(account)
        reclaim("u180", permissions=["invoice.refund"], actor="alice")
        recheck("u180")
        self.assertNotIn("u180", report()["pending_recheck"])

        # 复核后又发生授权，账号重新进入待复核
        grant("u180", "audit.read", actor="bob")
        self.assertIn("u180", report()["pending_recheck"])
        again = recheck("u180")
        self.assertFalse(again["already_reviewed"])

    def test_recheck_unknown_account_raises(self):
        with self.assertRaises(ValueError):
            recheck("ghost")


if __name__ == "__main__":
    unittest.main()
