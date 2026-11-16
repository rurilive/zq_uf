import unittest

from sqldetect import PAYLOADS, check, load, report

URL = "http://app.local/item"
RESPONSES = {
    "id": "You have an error in your SQL syntax near id",
    "name": "welcome back",
    "page": "unknown column error",
}


class CheckTest(unittest.TestCase):
    def setUp(self):
        load(URL, RESPONSES)

    def test_each_payload_adds_one_finding(self):
        out = check(URL, ["id"])
        self.assertEqual(len(out["findings"]), len(PAYLOADS))
        self.assertEqual([item["payload"] for item in out["findings"]], PAYLOADS)

    def test_blind_param_not_detected(self):
        out = check(URL, ["name"])
        self.assertEqual(out["findings"], [])

    def test_generic_error_text_not_detected(self):
        out = check(URL, ["page"])
        self.assertEqual(out["findings"], [])

    def test_report_counts_duplicates(self):
        check(URL, ["id", "page"])
        rep = report()
        self.assertEqual(rep["findings"], 3)
        self.assertEqual(rep["params"], {"id": 3})
        self.assertEqual(rep["duplicates"], 2)

    def test_uncovered_lists_stay_empty(self):
        out = check(URL, ["id"])
        self.assertEqual(out["pending"], [])
        self.assertEqual(out["skipped"], [])

    def test_reject_empty_params(self):
        with self.assertRaises(ValueError):
            check(URL, [])


if __name__ == "__main__":
    unittest.main()
