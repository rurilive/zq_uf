import unittest

from authmatrix import matrix, report, reset

NOW = 1_700_000_000
PAST = NOW - 3600
FUTURE = NOW + 3600


def full_results(**overrides):
    results = {
        "anonymous": "deny",
        "expired": {"actual": "deny", "expires_at": PAST},
        "other_user": "deny",
        "low_privilege": "deny",
    }
    results.update(overrides)
    return results


class MatrixTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_covers_all_four_identities(self):
        got = matrix({"api": "/admin/users", "results": full_results(), "now": NOW})
        self.assertEqual(
            got["covered"], ["anonymous", "expired", "other_user", "low_privilege"]
        )
        self.assertEqual(got["failed"], [])
        self.assertEqual(got["missing"], [])

    def test_failed_combo_recorded_per_identity(self):
        got = matrix({
            "api": "/admin/orders",
            "results": full_results(other_user="allow"),
            "now": NOW,
        })
        self.assertEqual(got["failed"], ["other_user"])
        self.assertIn("anonymous", got["covered"])

    def test_combinations_do_not_overwrite_each_other(self):
        matrix({"api": "/a", "results": {"anonymous": "allow"}, "now": NOW})
        got = matrix({"api": "/a", "results": {"low_privilege": "deny"}, "now": NOW})
        self.assertEqual(got["failed"], ["anonymous"])
        self.assertEqual(got["covered"], ["anonymous", "low_privilege"])

    def test_regression_skips_previously_passed(self):
        matrix({"api": "/a", "results": full_results(), "now": NOW})
        got = matrix({"api": "/a", "results": full_results(), "now": NOW})
        reasons = {item["identity"]: item["reason"] for item in got["skipped"]}
        self.assertEqual(
            set(reasons), {"anonymous", "expired", "other_user", "low_privilege"}
        )
        self.assertTrue(all(r == "previously passed" for r in reasons.values()))

    def test_regression_reruns_failed_and_marks_fixed(self):
        matrix({"api": "/a", "results": full_results(other_user="allow"), "now": NOW})
        got = matrix({"api": "/a", "results": full_results(), "now": NOW})
        self.assertEqual(got["failed"], [])
        self.assertEqual(got["diff"]["fixed"], ["other_user"])

    def test_regression_detects_regressed_with_force(self):
        matrix({"api": "/a", "results": full_results(), "now": NOW})
        got = matrix({
            "api": "/a",
            "results": full_results(low_privilege="allow"),
            "now": NOW,
            "force": True,
        })
        self.assertEqual(got["diff"]["regressed"], ["low_privilege"])
        self.assertEqual(got["failed"], ["low_privilege"])

    def test_new_combo_in_diff(self):
        matrix({"api": "/a", "results": {"anonymous": "deny"}, "now": NOW})
        got = matrix({"api": "/a", "results": {"low_privilege": "deny"}, "now": NOW})
        self.assertEqual(got["diff"]["new"], ["low_privilege"])

    def test_expired_credential_rejected_is_covered(self):
        got = matrix({
            "api": "/a",
            "results": {"expired": {"actual": "deny", "expires_at": PAST}},
            "now": NOW,
        })
        self.assertEqual(got["covered"], ["expired"])
        self.assertEqual(got["untested"], [])

    def test_expired_credential_still_valid_is_untested(self):
        got = matrix({
            "api": "/a",
            "results": {"expired": {"actual": "deny", "expires_at": FUTURE}},
            "now": NOW,
        })
        self.assertEqual(got["covered"], [])
        self.assertEqual(got["untested"][0]["identity"], "expired")
        self.assertIn("still valid", got["untested"][0]["reason"])

    def test_expired_requires_expires_at(self):
        with self.assertRaises(ValueError):
            matrix({"api": "/a", "results": {"expired": "deny"}, "now": NOW})

    def test_skip_reason_kept(self):
        got = matrix({
            "api": "/a",
            "results": {"low_privilege": {"skip": "no low-privilege account"}},
            "now": NOW,
        })
        self.assertEqual(
            got["skipped"],
            [{"identity": "low_privilege", "reason": "no low-privilege account"}],
        )

    def test_skip_does_not_erase_previous_failure(self):
        matrix({"api": "/a", "results": {"anonymous": "allow"}, "now": NOW})
        got = matrix({
            "api": "/a",
            "results": {"anonymous": {"skip": "target down"}},
            "now": NOW,
        })
        self.assertEqual(got["failed"], ["anonymous"])

    def test_report_aggregates_per_api(self):
        matrix({"api": "/a", "results": full_results(), "now": NOW})
        matrix({"api": "/b", "results": full_results(other_user="allow"), "now": NOW})
        got = report()
        self.assertEqual(got["apis"], 2)
        self.assertEqual(got["combinations"], 8)
        self.assertEqual(got["failed"], [{"api": "/b", "identity": "other_user"}])
        self.assertEqual(got["per_api"]["/a"]["missing"], [])

    def test_report_diff_categories(self):
        matrix({"api": "/a", "results": full_results(other_user="allow"), "now": NOW})
        matrix({"api": "/a", "results": full_results(), "now": NOW})
        matrix({"api": "/b", "results": {"anonymous": "deny"}, "now": NOW})
        diff = report()["diff"]
        self.assertEqual(diff["fixed"], [{"api": "/a", "identity": "other_user"}])
        self.assertIn({"api": "/b", "identity": "anonymous"}, diff["new"])
        self.assertEqual(diff["regressed"], [])

    def test_actual_shorthand_maps_to_anonymous(self):
        got = matrix({"api": "/a", "expected": "deny", "actual": "allow"})
        self.assertEqual(got["failed"], ["anonymous"])

    def test_api_required(self):
        with self.assertRaises(ValueError):
            matrix({"expected": "deny"})

    def test_unknown_identity_rejected(self):
        with self.assertRaises(ValueError):
            matrix({"api": "/a", "results": {"root": "allow"}})


if __name__ == "__main__":
    unittest.main()
