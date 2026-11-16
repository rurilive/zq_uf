import unittest

from vulnticket import report, update


class TicketTest(unittest.TestCase):
    def test_new_ticket_is_open(self):
        result = update({"id": "T-1", "vuln": "V-9", "project": "shop"})
        self.assertEqual(result["status"], "open")
        self.assertEqual(result["changes"], 0)
        self.assertIsNone(result["merged_into"])

    def test_close_without_retest(self):
        result = update({"id": "T-2", "vuln": "V-10", "project": "shop", "status": "closed"})
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["retests"], [])

    def test_duplicate_vuln_kept_apart(self):
        update({"id": "T-3", "vuln": "V-11", "project": "shop"})
        update({"id": "T-4", "vuln": "V-11", "project": "shop"})
        ids = [row["id"] for row in report("shop")["tickets"]]
        self.assertIn("T-3", ids)
        self.assertIn("T-4", ids)

    def test_duplicates_list_empty(self):
        update({"id": "T-5", "vuln": "V-12", "project": "blog"})
        self.assertEqual(report("blog")["duplicates"], [])

    def test_stuck_list_empty(self):
        update({"id": "T-6", "vuln": "V-13", "project": "blog"})
        self.assertEqual(report("blog")["stuck"], [])


if __name__ == "__main__":
    unittest.main()
