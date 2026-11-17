import unittest
from datetime import datetime, timedelta, timezone

from authmatrix import (
    ExpiredCredentialError,
    expired_credential,
    matrix,
    report,
    reset,
)

IDENTITIES = ["anonymous", "expired", "other_user", "low_privilege"]


def full(api, **overrides):
    """四种身份都给 deny 结论，按需覆盖个别身份。"""
    cases = {identity: "deny" for identity in IDENTITIES}
    cases["expired"] = {"actual": "deny", "validity": 900}
    cases.update(overrides)
    return matrix(dict({"api": api, "expected": "deny"}, cases=cases))


class CoverageTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_four_identities_covered_independently(self):
        got = full("/admin/users")
        self.assertEqual(got["covered"], IDENTITIES)
        self.assertEqual(got["missing"], [])
        self.assertEqual(got["failed"], [])

    def test_no_credentials_only_is_not_full_coverage(self):
        got = matrix({"api": "/admin/users", "anonymous": "deny"})
        self.assertEqual(got["covered"], ["anonymous"])
        self.assertEqual(got["missing"], ["expired", "other_user", "low_privilege"])

    def test_results_do_not_overwrite_each_other(self):
        matrix({"api": "/a", "identity": "anonymous", "actual": "deny"})
        matrix({"api": "/a", "identity": "other_user", "actual": "allow"})
        matrix({"api": "/a", "identity": "low_privilege", "actual": "deny"})
        got = matrix({"api": "/a", "identity": "expired", "actual": "deny", "validity": 60})
        self.assertEqual(got["covered"], ["anonymous", "expired", "other_user", "low_privilege"])
        self.assertEqual(got["failed"], ["other_user"])
        self.assertEqual(got["results"]["other_user"]["actual"], "allow")
        self.assertEqual(got["results"]["anonymous"]["actual"], "deny")

    def test_failure_is_reported_per_identity(self):
        got = full("/admin/orders", other_user="allow")
        self.assertEqual(got["failed"], ["other_user"])
        self.assertEqual(got["results"]["other_user"]["expected"], "deny")
        self.assertEqual(got["results"]["anonymous"]["status"], "pass")

    def test_skip_is_not_coverage(self):
        got = full("/a", low_privilege={"status": "skip", "reason": "no account"})
        self.assertEqual(got["covered"], ["anonymous", "expired", "other_user"])
        self.assertEqual(got["skipped"],
                         [{"identity": "low_privilege", "reason": "no account"}])

    def test_skip_does_not_erase_existing_conclusion(self):
        full("/a")
        got = matrix({"api": "/a", "identity": "other_user",
                      "status": "skip", "reason": "flaky"})
        self.assertEqual(got["covered"], IDENTITIES)
        self.assertEqual(got["skipped"], [])

    def test_api_required(self):
        with self.assertRaises(ValueError):
            matrix({"expected": "deny"})
        with self.assertRaises(ValueError):
            matrix({"api": "  "})

    def test_unknown_identity_rejected(self):
        with self.assertRaises(ValueError):
            matrix({"api": "/a", "cases": {"admin": "deny"}})

    def test_unknown_actual_rejected(self):
        with self.assertRaises(ValueError):
            matrix({"api": "/a", "identity": "anonymous", "actual": "maybe"})

    def test_bad_case_leaves_no_partial_record(self):
        with self.assertRaises(ValueError):
            matrix({"api": "/a", "cases": {"anonymous": "deny", "other_user": "huh"}})
        self.assertEqual(report()["apis"], 0)

    def test_single_case_legacy_shape(self):
        got = matrix({"api": "/a", "expected": "deny", "actual": "allow"})
        self.assertEqual(got["failed"], ["anonymous"])


class ExpiredTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_expiry_generated_from_actual_validity(self):
        expiry = expired_credential(900, issued_at="2026-01-01T00:00:00Z",
                                    now="2026-01-01T01:00:00Z")
        self.assertEqual(expiry["validity_seconds"], 900)
        self.assertEqual(expiry["expires_at"], "2026-01-01T00:15:00+00:00")
        self.assertEqual(expiry["checked_at"], "2026-01-01T01:00:00+00:00")
        self.assertGreater(expiry["expired_for_seconds"], 0)
        self.assertTrue(expiry["expired"])

    def test_checked_before_expiry_is_pushed_past_validity(self):
        expiry = expired_credential(60, issued_at="2026-01-01T00:00:00Z",
                                    now="2026-01-01T00:00:30Z")
        self.assertGreater(datetime.fromisoformat(expiry["checked_at"]),
                           datetime.fromisoformat(expiry["expires_at"]))

    def test_accepts_timedelta_and_epoch(self):
        issued = datetime(2026, 1, 1, tzinfo=timezone.utc)
        expiry = expired_credential(timedelta(minutes=5),
                                    issued_at=issued.timestamp(),
                                    now=issued + timedelta(hours=1))
        self.assertEqual(expiry["validity_seconds"], 300)
        self.assertTrue(expiry["expired"])

    def test_non_positive_validity_rejected(self):
        with self.assertRaises(ExpiredCredentialError):
            expired_credential(0)
        with self.assertRaises(ExpiredCredentialError):
            expired_credential(-5)

    def test_expired_result_needs_validity(self):
        with self.assertRaises(ExpiredCredentialError):
            matrix({"api": "/a", "identity": "expired", "actual": "deny"})

    def test_rejected_and_not_tested_are_distinct(self):
        matrix({"api": "/a", "identity": "expired", "actual": "deny", "validity": 60})
        matrix({"api": "/b", "identity": "expired", "status": "skip",
                "reason": "token service down"})
        matrix({"api": "/c"})
        got = report()["expired"]
        self.assertEqual([i["api"] for i in got["rejected"]], ["/a"])
        self.assertEqual(got["bypassed"], [])
        reasons = {i["api"]: i["reason"] for i in got["not_tested"]}
        self.assertEqual(reasons["/b"], "token service down")
        self.assertEqual(reasons["/c"], "missing")
        self.assertNotIn("/b", [i["api"] for i in got["rejected"]])

    def test_bypassed_expired_credential(self):
        matrix({"api": "/a", "identity": "expired", "actual": "allow", "validity": 60})
        got = report()["expired"]
        self.assertEqual([i["api"] for i in got["bypassed"]], ["/a"])
        self.assertEqual(got["rejected"], [])

    def test_expiry_is_kept_with_result(self):
        got = matrix({"api": "/a", "identity": "expired", "actual": "deny", "validity": 60})
        expiry = got["results"]["expired"]["expiry"]
        self.assertEqual(expiry["validity_seconds"], 60)
        self.assertTrue(expiry["checked_at"] > expiry["expires_at"])


class ReportTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_counts_combinations_per_api(self):
        full("/a")
        matrix({"api": "/b", "anonymous": "deny"})
        rep = report()
        self.assertEqual(rep["apis"], 2)
        self.assertEqual(rep["combinations"], 5)
        self.assertEqual(rep["expected_combinations"], 8)
        self.assertAlmostEqual(rep["coverage"], 5 / 8)

    def test_per_api_summary(self):
        full("/a", other_user="allow")
        matrix({"api": "/b", "anonymous": "deny"})
        per_api = report()["per_api"]
        self.assertEqual(sorted(per_api), ["/a", "/b"])
        self.assertEqual(per_api["/a"]["failed"], ["other_user"])
        self.assertEqual(per_api["/b"]["missing"], ["expired", "other_user", "low_privilege"])

    def test_missing_combinations_can_be_rerun_directly(self):
        matrix({"api": "/a", "anonymous": "deny"})
        rerun = report()["rerun"]
        self.assertIn({"api": "/a", "identity": "other_user", "reason": "missing"}, rerun)
        matrix({"api": "/a", "identity": "other_user", "actual": "deny"})
        self.assertNotIn(
            {"api": "/a", "identity": "other_user", "reason": "missing"},
            report()["rerun"],
        )

    def test_regression_diff_across_reports(self):
        full("/a")
        first = report()["diff"]
        self.assertEqual(first["new"], [
            {"api": "/a", "identity": i} for i in IDENTITIES
        ])
        self.assertEqual(report()["diff"], {"new": [], "fixed": [], "regressed": []})

        matrix({"api": "/a", "identity": "other_user", "actual": "allow"})
        self.assertEqual(report()["diff"]["regressed"],
                         [{"api": "/a", "identity": "other_user"}])

        matrix({"api": "/a", "identity": "other_user", "actual": "deny"})
        self.assertEqual(report()["diff"]["fixed"],
                         [{"api": "/a", "identity": "other_user"}])

    def test_new_combination_after_baseline(self):
        full("/a")
        report()
        matrix({"api": "/b", "anonymous": "deny"})
        diff = report()["diff"]
        self.assertEqual(diff["new"], [{"api": "/b", "identity": "anonymous"}])

    def test_failed_combinations_listed_per_api(self):
        full("/a", other_user="allow", low_privilege="allow")
        full("/b")
        failed = report()["failed"]
        self.assertEqual(
            [(f["api"], f["identity"]) for f in failed],
            [("/a", "other_user"), ("/a", "low_privilege")],
        )


if __name__ == "__main__":
    unittest.main()
