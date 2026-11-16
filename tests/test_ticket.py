import unittest

import vulnticket.ticket as ticket_mod
from vulnticket import report, update


class TicketTest(unittest.TestCase):
    def setUp(self):
        ticket_mod._tickets.clear()

    def test_new_ticket_is_open(self):
        result = update({"id": "T-1", "vuln": "V-9", "project": "shop"})
        self.assertEqual(result["status"], "open")
        self.assertEqual(result["changes"], 0)
        self.assertIsNone(result["merged_into"])

    def test_close_without_retest_rejected(self):
        update({"id": "T-2", "vuln": "V-10", "project": "shop"})
        with self.assertRaises(ValueError):
            update({"id": "T-2", "status": "closed", "operator": "chen"})

    def test_full_flow_close_after_retest_pass(self):
        update({"id": "T-3", "vuln": "V-11", "project": "shop"})
        update({"id": "T-3", "status": "fixing", "owner": "chen", "operator": "li"})
        update({"id": "T-3", "status": "pending_retest",
                "evidence": "commit abc123", "operator": "chen"})
        with self.assertRaises(ValueError):
            update({"id": "T-3", "status": "closed", "operator": "li"})
        result = update({"id": "T-3", "retest": {"result": "pass", "by": "li"}})
        self.assertEqual(len(result["retests"]), 1)
        result = update({"id": "T-3", "status": "closed", "operator": "li"})
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["changes"], 3)

    def test_fixing_requires_owner(self):
        update({"id": "T-4", "vuln": "V-12", "project": "shop"})
        with self.assertRaises(ValueError):
            update({"id": "T-4", "status": "fixing", "operator": "li"})

    def test_pending_retest_requires_evidence(self):
        update({"id": "T-5", "vuln": "V-13", "project": "shop"})
        update({"id": "T-5", "status": "fixing", "owner": "chen", "operator": "li"})
        with self.assertRaises(ValueError):
            update({"id": "T-5", "status": "pending_retest", "operator": "chen"})

    def test_retest_fail_returns_to_fixing(self):
        update({"id": "T-6", "vuln": "V-14", "project": "shop"})
        update({"id": "T-6", "status": "fixing", "owner": "chen", "operator": "li"})
        update({"id": "T-6", "status": "pending_retest",
                "evidence": "commit def456", "operator": "chen"})
        result = update({"id": "T-6", "retest": {"result": "fail", "by": "li", "note": "仍可复现"}})
        self.assertEqual(result["status"], "fixing")
        self.assertEqual(report("shop")["reject_reasons"], {"retest_fail": 1})

    def test_manual_return_requires_reason(self):
        update({"id": "T-7", "vuln": "V-15", "project": "shop"})
        update({"id": "T-7", "status": "fixing", "owner": "chen", "operator": "li"})
        update({"id": "T-7", "status": "pending_retest",
                "evidence": "commit ghi789", "operator": "chen"})
        with self.assertRaises(ValueError):
            update({"id": "T-7", "status": "fixing", "operator": "li"})
        result = update({"id": "T-7", "status": "fixing", "operator": "li",
                         "reason": {"type": "fix_incomplete", "detail": "漏改一处"}})
        self.assertEqual(result["status"], "fixing")
        self.assertEqual(report("shop")["reject_reasons"], {"fix_incomplete": 1})

    def test_history_records_from_to_operator(self):
        update({"id": "T-8", "vuln": "V-16", "project": "shop"})
        update({"id": "T-8", "status": "fixing", "owner": "chen", "operator": "li"})
        row = [t for t in report("shop")["tickets"] if t["id"] == "T-8"][0]
        last = row["history"][-1]
        self.assertEqual((last["from"], last["to"], last["operator"]), ("open", "fixing", "li"))

    def test_duplicate_vuln_merged_keeps_ids(self):
        update({"id": "T-9", "vuln": "V-17", "project": "shop"})
        result = update({"id": "T-10", "vuln": "V-17", "project": "shop"})
        self.assertEqual(result["status"], "merged")
        self.assertEqual(result["merged_into"], "T-9")
        data = report("shop")
        self.assertEqual(data["duplicates"], [{"ticket": "T-10", "merged_into": "T-9"}])
        ids = [row["id"] for row in data["tickets"]]
        self.assertIn("T-9", ids)
        self.assertIn("T-10", ids)
        master = [row for row in data["tickets"] if row["id"] == "T-9"][0]
        self.assertEqual(master["merged"], ["T-10"])

    def test_same_vuln_other_project_not_merged(self):
        update({"id": "T-11", "vuln": "V-18", "project": "shop"})
        result = update({"id": "T-12", "vuln": "V-18", "project": "blog"})
        self.assertEqual(result["status"], "open")

    def test_vuln_id_mismatch_rejected(self):
        update({"id": "T-13", "vuln": "V-19", "project": "shop"})
        with self.assertRaises(ValueError):
            update({"id": "T-13", "vuln": "V-20"})

    def test_stuck_listed_separately(self):
        update({"id": "T-14", "vuln": "V-21", "project": "blog"})
        update({"id": "T-15", "vuln": "V-22", "project": "blog"})
        update({"id": "T-15", "status": "fixing", "owner": "chen", "operator": "li"})
        self.assertEqual(report("blog")["stuck"], ["T-14"])

    def test_multiple_retests_ordered_by_time(self):
        update({"id": "T-16", "vuln": "V-23", "project": "shop"})
        update({"id": "T-16", "status": "fixing", "owner": "chen", "operator": "li"})
        update({"id": "T-16", "status": "pending_retest",
                "evidence": "commit 1", "operator": "chen"})
        update({"id": "T-16", "retest": {"result": "fail", "by": "li"}})
        update({"id": "T-16", "status": "pending_retest",
                "evidence": "commit 2", "operator": "chen"})
        result = update({"id": "T-16", "retest": {"result": "pass", "by": "li"}})
        self.assertEqual([r["result"] for r in result["retests"]], ["fail", "pass"])


if __name__ == "__main__":
    unittest.main()
