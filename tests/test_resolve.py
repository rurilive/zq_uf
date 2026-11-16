import unittest

from subdomain import enumerate, load, report

ROWS = [
    {"name": "www.example.com", "ip": "10.0.0.5", "status": "ok"},
    {"name": "web.example.com", "ip": "10.0.0.5", "status": "ok"},
    {"name": "api.example.com", "ip": "10.0.0.9", "status": "ok"},
    {"name": "old.example.com", "ip": "", "status": "fail"},
    {"name": "slow.example.com", "ip": "", "status": "timeout"},
]


class EnumTest(unittest.TestCase):
    def setUp(self):
        load("example.com", ROWS)

    def test_same_ip_names_kept_as_two_entries(self):
        out = enumerate("example.com")
        self.assertEqual(out["total"], 5)
        self.assertEqual(out["aliases"], 0)
        self.assertEqual(len(out["entries"]), 5)

    def test_wildcard_and_invalid_not_separated(self):
        out = enumerate("example.com")
        self.assertEqual(out["wildcard"], 0)
        self.assertEqual(out["invalid"], 0)

    def test_report_counts_candidates_as_valid(self):
        rep = report("example.com")
        self.assertEqual(rep["valid"], 5)
        self.assertEqual(rep["failed"], 2)
        self.assertEqual(rep["timeout"], 0)

    def test_round_always_one(self):
        self.assertEqual(enumerate("example.com")["round"], 1)
        self.assertEqual(report("example.com")["round"], 1)

    def test_reject_unknown_domain(self):
        with self.assertRaises(ValueError):
            enumerate("nope.example.com")


if __name__ == "__main__":
    unittest.main()
