import unittest

from patchimpact import assess, report

patch = {
    "name": "libxml2",
    "version": "2.9.1",
    "components": [
        {"name": "portal", "version": "3.0", "depends_on": ["libxml2"], "environment": "prod"},
        {"name": "report", "version": "1.4", "depends_on": ["libxml2"], "environment": "test"},
        {"name": "exporter", "version": "2.1", "depends_on": ["report"], "environment": "prod"},
    ],
}


class ImpactTest(unittest.TestCase):
    def test_only_direct_dependents(self):
        names = [c["name"] for c in assess(patch)["components"]]
        self.assertEqual(names, ["portal", "report"])

    def test_level_is_one(self):
        result = assess(patch)
        self.assertEqual(result["max_level"], 1)
        self.assertEqual({c["level"] for c in result["components"]}, {1})

    def test_environments_merged(self):
        self.assertEqual(assess(patch)["environments"], ["prod", "test"])

    def test_no_excluded(self):
        self.assertEqual(assess(patch)["excluded"], [])

    def test_report_counts(self):
        assess(patch)
        result = report("libxml2")
        self.assertGreaterEqual(result["count"], 1)
        self.assertEqual(result["excluded"], [])


if __name__ == "__main__":
    unittest.main()
