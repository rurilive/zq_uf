import unittest

from authmatrix import matrix, report, reset


class MatrixTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_only_anonymous_covered(self):
        got = matrix({"api": "/admin/users", "expected": "deny"})
        self.assertEqual(got["covered"], ["anonymous"])

    def test_no_failure_even_when_open(self):
        got = matrix({"api": "/admin/orders", "expected": "deny", "actual": "allow"})
        self.assertEqual(got["failed"], [])

    def test_no_regression_diff(self):
        got = matrix({"api": "/admin/logs", "expected": "deny"})
        self.assertEqual(got["diff"], [])

    def test_report_counts_one_per_api(self):
        matrix({"api": "/a"})
        matrix({"api": "/b"})
        self.assertEqual(report()["combinations"], 2)

    def test_api_required(self):
        with self.assertRaises(ValueError):
            matrix({"expected": "deny"})
