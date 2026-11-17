import unittest

from permaudit import (
    audit,
    grant,
    reclaim,
    report,
    review,
    changes,
    reset,
    STALE_DAYS,
)

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

    def test_fresh_permission_is_flagged_unused_but_not_stale(self):
        account = dict(ACCOUNT, id="u101", permissions=["report.export"], used={})
        account["granted_day"] = 100
        account["now"] = 101
        body = audit(account)
        self.assertEqual(body["unused"], ["report.export"])
        self.assertEqual(body["stale"], [])
        # 新授权、没长期闲置的权限不进待收回清单。
        self.assertEqual(body["reclaim"], [])

    def test_stale_permission_is_listed_and_reclaimed_for_active(self):
        account = dict(
            ACCOUNT,
            id="u110",
            used={"invoice.read": 12, "invoice.refund": STALE_DAYS + 5},
        )
        body = audit(account)
        self.assertEqual(body["stale"], ["invoice.refund"])
        self.assertEqual(
            [entry["permission"] for entry in body["reclaim"]],
            ["invoice.refund"],
        )
        self.assertEqual(body["reclaim"][0]["reason"], "stale")

    def test_never_used_permission_becomes_stale_after_threshold(self):
        account = dict(
            ACCOUNT, id="u111", permissions=["report.export"], used={},
            granted_day=1, now=STALE_DAYS + 1,
        )
        body = audit(account)
        self.assertEqual(body["unused"], ["report.export"])
        self.assertEqual(body["stale"], ["report.export"])
        self.assertEqual(
            [entry["permission"] for entry in body["reclaim"]], ["report.export"]
        )

    def test_left_account_is_abnormal_and_all_perms_reclaimed(self):
        account = dict(ACCOUNT, id="u102", status="left")
        body = audit(account)
        self.assertEqual(len(body["abnormal"]), 1)
        self.assertEqual(body["abnormal"][0]["status"], "left")
        self.assertEqual(
            sorted(entry["permission"] for entry in body["reclaim"]),
            sorted(ACCOUNT["permissions"]),
        )
        self.assertTrue(all(entry["reason"] == "left" for entry in body["reclaim"]))

    def test_transferred_account_reclaims_perms_outside_current_role(self):
        catalog = {"support": ["invoice.read"], "finance": []}
        account = dict(
            ACCOUNT,
            id="u120",
            role="support",
            status="transferred",
            role_permissions=catalog,
        )
        body = audit(account)
        self.assertEqual(body["abnormal"][0]["status"], "transferred")
        reclaimed = {entry["permission"]: entry["reason"] for entry in body["reclaim"]}
        # 现岗位范围内的权限保留，原岗位残留按 transferred 收回。
        self.assertNotIn("invoice.read", reclaimed)
        self.assertEqual(reclaimed["invoice.refund"], "transferred")
        self.assertEqual(reclaimed["user.export"], "transferred")

    def test_status_alias_normalizes_departed_to_left(self):
        body = audit(dict(ACCOUNT, id="u121", status="departed"))
        self.assertEqual(body["status"], "left")
        self.assertEqual(body["abnormal"][0]["status"], "left")

    def test_report_groups_reclaim_by_system_and_role(self):
        audit(dict(ACCOUNT, id="u130", status="left"))
        audit(dict(ACCOUNT, id="u131", status="left", role="finance"))
        body = report()
        self.assertEqual(body["systems"]["billing"], 2)
        groups = body["reclaim_list"]["billing"]
        self.assertEqual(
            sorted(entry["account"] for entry in groups["support"]),
            ["u130", "u130", "u130"],
        )
        self.assertEqual(
            sorted(entry["account"] for entry in groups["finance"]),
            ["u131", "u131", "u131"],
        )
        self.assertEqual(body["reclaim"], 6)

    def test_dormant_accounts_counted_separately(self):
        audit(dict(ACCOUNT, id="u140", last_login=200))
        audit(dict(ACCOUNT, id="u141", last_login=10))
        # 没有 last_login 也没有任何使用记录：视为从未登录。
        audit(dict(ACCOUNT, id="u142", used={}, last_login=None))
        body = report()
        dormant = {entry["account"]: entry["last_login"] for entry in body["dormant"]["accounts"]}
        self.assertEqual(body["dormant"]["count"], 2)
        self.assertEqual(dormant["u140"], 200)
        self.assertIsNone(dormant["u142"])
        self.assertNotIn("u141", dormant)

    def test_grant_records_actor_and_time(self):
        audit(dict(ACCOUNT, id="u104"))
        result = grant("u104", "audit.read", actor="alice", time=50)
        self.assertTrue(result["recorded"])
        self.assertEqual(result["actor"], "alice")
        self.assertEqual(result["time"], 50)
        log = changes("u104")
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["action"], "grant")
        self.assertEqual(log[0]["permission"], "audit.read")

    def test_change_log_is_sorted_by_time(self):
        audit(dict(ACCOUNT, id="u150"))
        grant("u150", "perm.a", actor="alice", time=30)
        grant("u150", "perm.b", actor="bob", time=10)
        grant("u150", "perm.c", actor="carol", time=20)
        log = changes()
        self.assertEqual(
            [(entry["time"], entry["permission"]) for entry in log],
            [(10, "perm.b"), (20, "perm.c"), (30, "perm.a")],
        )

    def test_reclaim_reports_impact_then_removes(self):
        account = dict(ACCOUNT, id="u160", status="left")
        audit(account)
        result = reclaim("u160", ["invoice.refund"], actor="admin", time=60)
        self.assertEqual(result["impact"]["system"], "billing")
        self.assertEqual(result["impact"]["role"], "support")
        self.assertEqual(result["impact"]["affected"], 1)
        self.assertEqual(result["removed"], ["invoice.refund"])
        # 用收回后的实际授权重新审计，确认权限确实从账号上移除。
        account["permissions"] = [
            perm for perm in account["permissions"] if perm != "invoice.refund"
        ]
        body = audit(account)
        self.assertNotIn("invoice.refund", body["granted"])
        self.assertEqual(
            [entry["action"] for entry in changes("u160")], ["reclaim"]
        )

    def test_reclaim_defaults_to_pending_list(self):
        audit(dict(ACCOUNT, id="u161", status="left"))
        result = reclaim("u161", actor="admin")
        self.assertEqual(result["affected"], 3)
        self.assertEqual(report()["reclaim"], 0)

    def test_only_changed_accounts_need_review_and_review_once(self):
        audit(dict(ACCOUNT, id="u170"))
        self.assertEqual(report()["review"], [])
        grant("u170", "audit.read", actor="alice", time=1)
        self.assertEqual(report()["review"], ["u170"])
        record = review("u170", actor="auditor", time=2)
        self.assertEqual(record["action"], "review")
        self.assertEqual(report()["review"], [])
        with self.assertRaises(ValueError):
            review("u170", actor="auditor", time=3)

    def test_review_state_survives_reaudit(self):
        account = dict(ACCOUNT, id="u180")
        audit(account)
        grant("u180", "audit.read", actor="alice", time=1)
        audit(account)
        self.assertEqual(report()["review"], ["u180"])

    def test_unknown_account_raises(self):
        with self.assertRaises(ValueError):
            grant("nobody", "x")
        with self.assertRaises(ValueError):
            reclaim("nobody")
        with self.assertRaises(ValueError):
            review("nobody")


if __name__ == "__main__":
    unittest.main()
