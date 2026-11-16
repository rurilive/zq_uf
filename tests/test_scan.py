import unittest

from covmatrix import report, scan


class ScanTest(unittest.TestCase):
    def test_fixed_rate(self):
        self.assertEqual(scan({"host": "10.0.0.5"})["rate"], 100)

    def test_rescan_starts_from_zero(self):
        scan({"host": "10.0.0.6"})
        self.assertEqual(scan({"host": "10.0.0.6"})["resume"], 0)

    def test_throttle_hint_ignored(self):
        result = scan({"host": "10.0.0.7", "throttle": "429 too many requests"})
        self.assertIsNone(result["throttle"])

    def test_same_rate_for_all(self):
        rates = {scan({"host": "10.0.0.8"})["rate"], scan({"host": "10.1.0.9"})["rate"]}
        self.assertEqual(rates, {100})

    def test_report_has_no_adjustments(self):
        scan({"host": "10.0.0.10"})
        result = report("10.0.0.10")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["adjustments"], [])


if __name__ == "__main__":
    unittest.main()
