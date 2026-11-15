import unittest

from failclass import SiteLedger, classify, policy


def resp(status, headers=None):
    return {"status_code": status, "headers": headers or {}}


class ClassifyTest(unittest.TestCase):
    def test_timeout_by_error_text(self):
        self.assertEqual(classify("read timeout")["reason"], "timeout")

    def test_not_found_by_status(self):
        self.assertEqual(classify("HTTP 404", resp(404))["reason"], "not_found")

    def test_banned_by_status(self):
        self.assertEqual(classify("HTTP 403", resp(403))["reason"], "banned")

    def test_unknown_is_error(self):
        self.assertEqual(classify("connection reset")["reason"], "error")

    def test_evidence_has_status_and_headers(self):
        ev = classify("x", resp(403, {"retry-after": "600"}))["evidence"]
        self.assertEqual(ev["status_code"], 403)
        self.assertEqual(ev["headers"], {"retry-after": "600"})


class PolicyTest(unittest.TestCase):
    def test_timeout_retries_with_delay(self):
        p = policy("timeout")
        self.assertEqual(p["action"], "retry")
        self.assertGreater(p["delay"], 0)

    def test_banned_switches_exit_and_cools_down(self):
        p = policy("banned")
        self.assertEqual(p["action"], "cooldown")
        self.assertTrue(p["switch_exit"])

    def test_not_found_gives_up(self):
        self.assertEqual(policy("not_found")["action"], "give_up")

    def test_budget_is_site(self):
        self.assertEqual(policy("error")["budget"], "site")


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.clock = [1000.0]
        self.ledger = SiteLedger(site_budget=2, now=lambda: self.clock[0])

    def test_budget_shared_across_tasks_of_same_site(self):
        self.ledger.record("s", "t1", "timeout")
        r = self.ledger.record("s", "t2", "timeout")
        self.assertEqual(r["remaining"], 0)
        # 第三次重试预算耗尽，降级为放弃并进复核清单
        r = self.ledger.record("s", "t3", "timeout")
        self.assertEqual(r["action"], "give_up")
        self.assertEqual(len(self.ledger.review_list("s")), 1)

    def test_budget_not_shared_across_sites(self):
        self.ledger.record("a", "t1", "timeout")
        r = self.ledger.record("b", "t2", "timeout")
        self.assertEqual(r["remaining"], 1)

    def test_ban_cooldown_recovers_automatically(self):
        self.ledger.record("s", "t1", "HTTP 403", resp(403))
        self.assertFalse(self.ledger.site_available("s"))
        self.clock[0] += 601
        self.assertTrue(self.ledger.site_available("s"))

    def test_not_found_goes_to_review_immediately(self):
        r = self.ledger.record("s", "t1", "HTTP 404", resp(404))
        self.assertEqual(r["action"], "give_up")
        self.assertEqual(self.ledger.review_list("s")[0]["task"], "t1")

    def test_breakdown_by_site(self):
        self.ledger.record("s", "t1", "timeout")
        self.ledger.record("s", "t2", "HTTP 403", resp(403))
        self.assertEqual(self.ledger.breakdown("s"),
                         {"timeout": 1, "banned": 1})

    def test_records_traceable_to_request(self):
        r = self.ledger.record("s", "t1", "HTTP 403", resp(403))
        rec = self.ledger.records("s", reason="banned")[0]
        self.assertEqual(rec["id"], r["trace_id"])
        self.assertEqual(rec["evidence"]["status_code"], 403)

    def test_requeue_does_not_consume_budget(self):
        self.ledger.record("s", "t1", "HTTP 404", resp(404))
        self.assertTrue(self.ledger.requeue("s", "t1"))
        self.assertEqual(self.ledger.review_list("s"), [])
        r = self.ledger.record("s", "t1", "timeout")
        self.assertEqual(r["action"], "retry")
        self.assertEqual(r["remaining"], 1)  # 预算仍按 2 次算

    def test_export_review_by_site(self):
        self.ledger.record("s", "t1", "HTTP 404", resp(404))
        out = self.ledger.export_review("s")
        self.assertIn('"site": "s"', out)
        self.assertIn('"task": "t1"', out)

    def test_new_day_resets_budget(self):
        self.ledger.record("s", "t1", "timeout")
        self.ledger.record("s", "t2", "timeout")
        self.ledger.new_day()
        r = self.ledger.record("s", "t3", "timeout")
        self.assertEqual(r["action"], "retry")


if __name__ == "__main__":
    unittest.main()
