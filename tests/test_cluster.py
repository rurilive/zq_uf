import unittest

from honeypot import cluster, report, reset


class ClusterTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_same_ip_one_group(self):
        out = cluster([
            {"ip": "203.0.113.7", "path": "/admin", "at": 10},
            {"ip": "203.0.113.7", "path": "/admin", "at": 12},
        ])
        self.assertEqual(out["sources"], 1)
        self.assertEqual(out["groups"][0]["count"], 2)
        self.assertEqual(out["groups"][0]["first"], 10)
        self.assertEqual(out["groups"][0]["last"], 12)

    def test_same_path_new_ip_is_new_group(self):
        out = cluster([
            {"ip": "203.0.113.7", "path": "/admin", "at": 10},
            {"ip": "198.51.100.4", "path": "/admin", "at": 11},
        ])
        self.assertEqual(out["sources"], 2)
        self.assertEqual([g["ip"] for g in out["groups"]], ["198.51.100.4", "203.0.113.7"])

    def test_no_feature_fields(self):
        out = cluster([{"ip": "203.0.113.7", "path": "/admin", "at": 10, "ua": "nikto"}])
        self.assertNotIn("features", out["groups"][0])
        self.assertNotIn("outliers", out)

    def test_report_orders_by_count(self):
        cluster([
            {"ip": "203.0.113.7", "path": "/admin", "at": 10},
            {"ip": "203.0.113.7", "path": "/admin", "at": 11},
            {"ip": "198.51.100.4", "path": "/.env", "at": 30},
        ])
        top = report()["top"]
        self.assertEqual(top[0]["ip"], "203.0.113.7")
        self.assertEqual(report()["events"], 3)

    def test_missing_path_raises(self):
        with self.assertRaises(ValueError):
            cluster([{"ip": "203.0.113.7", "at": 10}])


if __name__ == "__main__":
    unittest.main()
