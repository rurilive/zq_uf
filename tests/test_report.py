import unittest

from crawlreport import build, reconcile


class ReportTest(unittest.TestCase):
    def test_build_counts_rows(self):
        self.assertEqual(build({"id": "b", "rows": [1, 2, 3]})["total"], 3)

    def test_build_has_no_breakdown(self):
        self.assertEqual(build({"id": "b", "rows": [1]})["by_site"], {})

    def test_reconcile_matches(self):
        self.assertTrue(reconcile({"batch": "b"})["matched"])
