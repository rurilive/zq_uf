import unittest

from reportcheck import coverage, report

api = {
    "name": "GET /orders",
    "environment": "test",
    "types": {"sqli": "hit", "xss": "miss", "ssrf": "skip"},
}


class MatrixTest(unittest.TestCase):
    def test_only_hit_listed(self):
        result = coverage(dict(api))
        self.assertEqual([row["type"] for row in result["covered"]], ["sqli"])

    def test_untested_not_listed(self):
        result = coverage(dict(api))
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["skipped"], [])

    def test_pending_new_empty(self):
        self.assertEqual(coverage(dict(api))["pending_new"], [])

    def test_env_rows_not_split(self):
        coverage({"name": "GET /orders", "environment": "test", "types": {"sqli": "hit"}})
        coverage({"name": "GET /orders", "environment": "prod", "types": {"xss": "hit"}})
        envs = {row["environment"] for row in report("GET /orders")["rows"]}
        self.assertEqual(envs, {"test", "prod"})

    def test_report_pending_new_empty(self):
        coverage(dict(api))
        self.assertEqual(report()["pending_new"], [])


if __name__ == "__main__":
    unittest.main()
