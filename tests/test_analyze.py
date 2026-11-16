import unittest

from sandboxreport import parse, reset, summary

REPORT = {
    "id": "s-001",
    "behaviors": [
        {"type": "network", "action": "connect 10.0.0.5:443", "seconds": 12},
        {"type": "file", "action": "write /tmp/a.tmp", "seconds": 3},
        {"type": "network", "action": "connect 10.0.0.5:443", "seconds": 9},
    ],
    "static": {"packer": "upx", "entropy": 7.4},
}


class AnalyzeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_repeated_behavior_kept_twice(self):
        out = parse(REPORT)
        self.assertEqual(len(out["behaviors"]), 3)

    def test_order_follows_report(self):
        out = parse(REPORT)
        self.assertEqual([b["seconds"] for b in out["behaviors"]], [12, 3, 9])

    def test_static_features_passed_through(self):
        out = parse(REPORT)
        self.assertEqual(out["static"]["packer"], "upx")
        self.assertNotIn("anomalies", out)

    def test_summary_counts_entries(self):
        parse(REPORT)
        out = summary("s-001")
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["by_type"], {"network": 2, "file": 1})

    def test_unknown_format_raises_key_error(self):
        with self.assertRaises(KeyError):
            parse({"id": "s-002", "sections": []})


if __name__ == "__main__":
    unittest.main()
