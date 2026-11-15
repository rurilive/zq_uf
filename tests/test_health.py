import unittest

from sitehealth import report, score


class HealthTest(unittest.TestCase):
    def test_report_counts(self):
        self.assertGreaterEqual(report("a", {"ok": True})["events"], 1)

    def test_score_is_fixed(self):
        self.assertEqual(score("a")["score"], 1.0)

    def test_no_parts(self):
        self.assertEqual(score("a")["parts"], {})
