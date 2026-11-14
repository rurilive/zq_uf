import unittest

from uafilter import headers, report
from uafilter import ua


class UaTest(unittest.TestCase):
    def setUp(self):
        ua._stats.clear()

    def test_headers_has_ua(self):
        self.assertIn("User-Agent", headers("shop"))

    def test_same_headers_all_sites(self):
        self.assertEqual(headers("shop"), headers("news"))

    def test_report_counts(self):
        report("shop", 403)
        self.assertEqual(report("shop", 403)[403], 2)
