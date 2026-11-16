import unittest

from idormatrix import report, verify

API = {
    "name": "order.detail",
    "owner": "u1",
    "low_privilege": ["u3"],
    "cases": [
        {"as": "u1", "resource_owner": "u1", "status": 200},
        {"as": "u2", "resource_owner": "u1", "status": 200},
        {"as": "u3", "resource_owner": "u1", "status": 403},
        {"as": "guest", "resource_owner": "u1", "status": 401},
    ],
}


class VerifyTest(unittest.TestCase):
    def test_roles_are_derived_from_identity_and_owner(self):
        combos = {c["role"]: c for c in verify(API)["combos"]}
        self.assertEqual(combos["self"]["as"], "u1")
        self.assertEqual(combos["other"]["as"], "u2")
        self.assertEqual(combos["low"]["as"], "u3")
        self.assertEqual(combos["none"]["as"], "guest")

    def test_full_coverage_has_no_missing_combo(self):
        coverage = verify(API)["coverage"]
        self.assertEqual(coverage["covered"], ["self", "other", "low", "none"])
        self.assertEqual(coverage["missing"], [])

    def test_missing_combos_are_reported(self):
        api = {"name": "order.list", "cases": [{"as": "u2", "resource_owner": "u1", "status": 403}]}
        coverage = verify(api)["coverage"]
        self.assertEqual(coverage["covered"], ["other"])
        self.assertEqual(coverage["missing"], ["self", "low", "none"])

    def test_other_role_allowed_is_fail(self):
        failed = verify(API)["failed"]
        self.assertEqual([c["role"] for c in failed], ["other"])

    def test_error_is_not_counted_as_denied_or_fail(self):
        api = {
            "name": "order.export",
            "cases": [{"as": "u2", "resource_owner": "u1", "status": 500}],
        }
        result = verify(api)
        self.assertEqual(result["failed"], [])
        self.assertEqual([c["role"] for c in result["errors"]], ["other"])
        self.assertEqual(result["combos"][0]["conclusion"], "error")

    def test_results_are_counted_by_role(self):
        by_role = verify(API)["by_role"]
        self.assertEqual(by_role["self"]["allowed"], 1)
        self.assertEqual(by_role["other"]["allowed"], 1)
        self.assertEqual(by_role["low"]["denied"], 1)
        self.assertEqual(by_role["none"]["denied"], 1)

    def test_regression_diff_against_previous_run(self):
        verify(API)
        fixed = dict(API, cases=[dict(c, status=403) if c["as"] == "u2" else dict(c) for c in API["cases"]])
        regression = verify(fixed)["regression"]
        self.assertEqual(
            regression["changed"],
            [{"role": "other", "before": "allowed", "after": "denied"}],
        )

    def test_new_combo_shows_in_regression(self):
        api = {"name": "order.cancel", "cases": [{"as": "u2", "resource_owner": "u1", "status": 403}]}
        verify(api)
        api["cases"].append({"as": "guest", "resource_owner": "u1", "status": 401})
        self.assertEqual(verify(api)["regression"]["new"], ["none"])

    def test_report_aggregates_per_api_and_keeps_history(self):
        verify(API)
        verify(API)
        body = report()
        self.assertEqual(body["by_api"]["order.detail"]["failed"], 1)
        self.assertEqual(body["by_api"]["order.detail"]["missing"], [])
        self.assertGreaterEqual(len(body["history"]["order.detail"]), 2)
        self.assertIn("order.detail", body["regression"])

    def test_rerun_lists_failed_and_new_apis_only(self):
        verify(API)  # order.detail 有未通过组合
        ok = {"name": "order.ok", "cases": [{"as": "u2", "resource_owner": "u1", "status": 403}]}
        verify(ok)
        verify(ok)  # 第二次跑，不再是新增
        rerun = report()["rerun"]
        self.assertIn("order.detail", rerun)
        self.assertNotIn("order.ok", rerun)


if __name__ == "__main__":
    unittest.main()
