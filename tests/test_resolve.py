import unittest

from subdomain import diff, enumerate, load, report, restore, wildcards

ROWS = [
    {"name": "www.example.com", "ip": "10.0.0.5", "status": "ok"},
    {"name": "web.example.com", "ip": "10.0.0.5", "status": "ok"},
    {"name": "api.example.com", "ip": "10.0.0.9", "status": "ok"},
    {"name": "cdn.example.com", "ip": "10.0.0.99", "status": "wildcard"},
    {"name": "old.example.com", "ip": "", "status": "fail"},
    {"name": "slow.example.com", "ip": "", "status": "timeout"},
]


class EnumTest(unittest.TestCase):
    def test_same_ip_names_merged_into_one_entry(self):
        load("a.example.com", ROWS)
        out = enumerate("a.example.com")
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["aliases"], 1)
        merged = {e["ip"]: e["names"] for e in out["entries"]}
        self.assertEqual(merged["10.0.0.5"], ["www.example.com", "web.example.com"])

    def test_wildcard_and_invalid_separated(self):
        load("b.example.com", ROWS)
        out = enumerate("b.example.com")
        self.assertEqual(out["wildcard"], 1)
        self.assertEqual(out["invalid"], 2)
        self.assertEqual(out["total"], 2)

    def test_wildcard_keeps_original_record(self):
        load("c.example.com", ROWS)
        records = wildcards("c.example.com")
        self.assertEqual(records, [{"name": "cdn.example.com", "ip": "10.0.0.99", "status": "wildcard"}])

    def test_report_counts_entries_not_candidates(self):
        load("d.example.com", ROWS)
        rep = report("d.example.com")
        self.assertEqual(rep["valid"], 2)
        self.assertEqual(rep["aliases"], 1)
        self.assertEqual(rep["wildcard"], 1)
        self.assertEqual(rep["failed"], 1)
        self.assertEqual(rep["timeout"], 1)
        self.assertEqual(rep["total"], 6)

    def test_confirmed_invalid_skipped_on_reload(self):
        load("e.example.com", ROWS)
        out = load("e.example.com", [
            {"name": "old.example.com", "ip": "", "status": "fail"},
            {"name": "new.example.com", "ip": "10.0.0.7", "status": "ok"},
        ])
        self.assertEqual(out["skipped"], 1)
        self.assertEqual(out["round"], 2)
        self.assertEqual(enumerate("e.example.com")["invalid"], 2)

    def test_diff_shows_only_new_entries(self):
        load("f.example.com", ROWS)
        load("f.example.com", [
            {"name": "new.example.com", "ip": "10.0.0.7", "status": "ok"},
            {"name": "www.example.com", "ip": "10.0.0.5", "status": "ok"},
        ])
        out = diff("f.example.com")
        self.assertEqual(out["round"], 2)
        self.assertEqual(out["entries"], [{"ip": "10.0.0.7", "names": ["new.example.com"]}])
        self.assertEqual(report("f.example.com")["new"], 1)

    def test_restore_misjudged_record(self):
        load("g.example.com", ROWS)
        restore("g.example.com", "cdn.example.com")
        out = enumerate("g.example.com")
        self.assertEqual(out["wildcard"], 0)
        self.assertEqual(out["total"], 3)
        with self.assertRaises(ValueError):
            restore("g.example.com", "cdn.example.com")

    def test_reject_unknown_domain(self):
        with self.assertRaises(ValueError):
            enumerate("nope.example.com")
        with self.assertRaises(ValueError):
            report("nope.example.com")


if __name__ == "__main__":
    unittest.main()
