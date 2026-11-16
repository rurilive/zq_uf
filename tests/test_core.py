import unittest

from assetmerge import merge, report, scan_list

ASSETS = [
    {"id": "a1", "host": "web1", "kind": "domain", "value": "www.example.com",
     "vulns": ["vuln-sqli"]},
    {"id": "a2", "host": "web1", "kind": "ip", "value": "10.0.0.8",
     "vulns": ["vuln-sqli"]},
    {"id": "a3", "host": "web1", "kind": "port", "value": "10.0.0.8:443",
     "vulns": ["vuln-xss"]},
]


class MergeTest(unittest.TestCase):
    def test_same_host_kept_as_separate_records(self):
        out = merge(ASSETS)
        self.assertEqual(out["count"], 3)
        self.assertEqual(out["alias_count"], 0)
        self.assertEqual(out["basis"], "record")

    def test_vuln_counted_per_record(self):
        merge(ASSETS)
        rep = report()
        self.assertEqual(rep["hosts"], 3)
        self.assertEqual(rep["vulns"]["vuln-sqli"], 2)
        self.assertEqual(rep["vuln_total"], 3)

    def test_splits_empty_and_scan_list_follows_record_id(self):
        out = merge(ASSETS)
        self.assertEqual(out["splits"], [])
        self.assertEqual(scan_list(), ["a1", "a2", "a3"])

    def test_reject_non_list(self):
        with self.assertRaises(ValueError):
            merge("not-a-list")


if __name__ == "__main__":
    unittest.main()
