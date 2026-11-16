import unittest

from reachable import reachable, report

deps = {
    "project": "shop",
    "modules": [
        {"name": "log4j", "version": "2.14.1"},
        {"name": "commons-io", "version": "1.2"},
    ],
    "vulnerable": [
        {"name": "log4j", "version": "2.14.1"},
        {"name": "commons-io", "version": "1.2.0"},
    ],
}


class GraphTest(unittest.TestCase):
    def test_exact_version_is_reachable(self):
        result = reachable(dict(deps))
        self.assertEqual([hit["component"] for hit in result["reachable"]], ["log4j"])

    def test_path_is_empty(self):
        result = reachable(dict(deps))
        self.assertEqual(result["reachable"][0]["path"], [])
        self.assertIsNone(result["reachable"][0]["entry"])

    def test_version_format_mismatch_is_dropped(self):
        names = [hit["component"] for hit in reachable(dict(deps))["reachable"]]
        self.assertNotIn("commons-io", names)

    def test_unreachable_list_empty(self):
        self.assertEqual(reachable(dict(deps))["unreachable"], [])

    def test_report_counts(self):
        reachable(dict(deps))
        self.assertGreaterEqual(report("shop")["count"], 1)
        self.assertEqual(report("shop")["pending"], [])


if __name__ == "__main__":
    unittest.main()
