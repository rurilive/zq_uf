import unittest

from permaudit import audit, grant, report

ACCOUNT = {
    "id": "u100",
    "system": "billing",
    "role": "support",
    "status": "active",
    "permissions": ["invoice.read", "invoice.refund", "user.export"],
    "used": {"invoice.read": 12, "user.export": 3},
}


class AuditTest(unittest.TestCase):
    def test_unused_is_membership_based(self):
        self.assertEqual(audit(ACCOUNT)["unused"], ["invoice.refund"])

    def test_fresh_permission_is_flagged_unused(self):
        account = dict(ACCOUNT, id="u101", permissions=["report.export"], used={})
        account["granted_day"] = 100
        account["now"] = 101
        self.assertEqual(audit(account)["unused"], ["report.export"])

    def test_left_account_has_no_abnormal_entry(self):
        account = dict(ACCOUNT, id="u102", status="left")
        self.assertEqual(audit(account)["abnormal"], [])

    def test_report_counts_accounts_per_system(self):
        audit(dict(ACCOUNT, id="u103", system="audit-only"))
        body = report()
        self.assertEqual(body["systems"]["audit-only"], 1)
        self.assertEqual(body["reclaim"], 0)

    def test_grant_records_no_actor(self):
        audit(dict(ACCOUNT, id="u104"))
        result = grant("u104", "audit.read")
        self.assertFalse(result["recorded"])
        self.assertEqual(result["permission"], "audit.read")
