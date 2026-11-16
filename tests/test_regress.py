import unittest

from wafregress import regress, report, reset

RULES = [
    {"id": "r1", "pattern": "union select"},
    {"id": "r2", "pattern": "<script>"},
]


class RegressTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_attack_cases_hit(self):
        out = regress(RULES)
        self.assertEqual(out["cases"], 6)
        self.assertEqual(out["blocked"], 2)

    def test_missed_keeps_request(self):
        regress(RULES)
        missed = report()["missed"]
        self.assertEqual([m["case"] for m in missed], ["c3"])
        self.assertEqual(missed[0]["request"], "/login?user=admin'--")

    def test_business_case_block_counted_as_hit(self):
        regress([{"id": "r9", "pattern": "laptop"}])
        blocked = [b["case"] for b in report()["blocked"]]
        self.assertIn("c2", blocked)
        self.assertNotIn("false_positive", report())

    def test_report_before_run_is_empty(self):
        out = report()
        self.assertEqual(out["rules"], 0)
        self.assertEqual(out["blocked"], [])

    def test_rules_must_be_list(self):
        with self.assertRaises(ValueError):
            regress("union select")


if __name__ == "__main__":
    unittest.main()
