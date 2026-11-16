import unittest

from idormatrix import report, verify

API = {
    "name": "order.detail",
    "owner": "u1",
    "cases": [
        {"as": "u1", "resource_owner": "u1", "status": 200},
        {"as": "u2", "resource_owner": "u1", "status": 200},
        {"as": "u2", "resource_owner": "u1", "status": 403},
    ],
}


class VerifyTest(unittest.TestCase):
    def test_200_is_reported_as_fail(self):
        failed = verify(API)["failed"]
        self.assertEqual(len(failed), 2)
        self.assertEqual(failed[0]["status"], 200)

    def test_403_is_reported_as_pass(self):
        checks = verify(API)["checks"]
        self.assertEqual(checks[2]["result"], "pass")

    def test_server_error_counts_as_pass(self):
        api = {
            "name": "order.list",
            "cases": [{"as": "u2", "resource_owner": "u1", "status": 500}],
        }
        self.assertEqual(verify(api)["failed"], [])

    def test_no_regression_is_computed(self):
        self.assertEqual(verify(API)["regression"], [])

    def test_report_sums_failed_per_api(self):
        verify({"name": "order.audit", "cases": [{"as": "u2", "status": 200}]})
        body = report()
        self.assertEqual(body["by_api"]["order.audit"], 1)
        self.assertEqual(body["regressed"], 0)
