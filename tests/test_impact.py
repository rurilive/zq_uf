import unittest

from patchimpact import assess, diff, export, report, reverse_lookup
from patchimpact import impact

patch = {
    "name": "libxml2",
    "version": "2.9.1",
    "components": [
        {"name": "portal", "version": "3.0", "depends_on": ["libxml2"], "environment": "prod"},
        {"name": "report", "version": "1.4", "depends_on": ["libxml2"], "environment": "test"},
        {"name": "exporter", "version": "2.1", "depends_on": ["report"], "environment": "prod"},
    ],
}


def setUpModule():
    impact._assessments.clear()


class ImpactTest(unittest.TestCase):
    def setUp(self):
        impact._assessments.clear()

    def test_direct_and_indirect_dependents(self):
        names = [c["name"] for c in assess(patch)["components"]]
        self.assertEqual(names, ["exporter", "portal", "report"])  # 按组件名排序

    def test_dependency_levels(self):
        result = assess(patch)
        levels = {c["name"]: c["level"] for c in result["components"]}
        self.assertEqual(levels, {"portal": 1, "report": 1, "exporter": 2})
        self.assertEqual(result["max_level"], 2)
        self.assertEqual(result["components"][0]["path"], ["libxml2", "report", "exporter"])

    def test_environments_merged(self):
        self.assertEqual(assess(patch)["environments"], ["prod", "test"])

    def test_no_excluded(self):
        self.assertEqual(assess(patch)["excluded"], [])

    def test_version_range_exclusion_with_reason(self):
        p = {
            "name": "libxml2",
            "version": "2.9.1",
            "components": [
                {"name": "portal", "version": "3.0", "depends_on": ["libxml2"],
                 "requires": {"libxml2": ">=2.0,<3.0"}, "environment": "prod"},
                {"name": "legacy", "version": "1.0", "depends_on": ["libxml2"],
                 "requires": {"libxml2": "<2.0"}, "environment": "prod"},
            ],
        }
        result = assess(p)
        self.assertEqual([c["name"] for c in result["components"]], ["portal"])
        self.assertEqual(len(result["excluded"]), 1)
        self.assertEqual(result["excluded"][0]["name"], "legacy")
        self.assertIn("<2.0", result["excluded"][0]["reason"])

    def test_deep_level_risk_flag(self):
        p = {
            "name": "libxml2",
            "version": "2.9.1",
            "components": [
                {"name": "a", "version": "1.0", "depends_on": ["libxml2"], "environment": "prod"},
                {"name": "b", "version": "1.0", "depends_on": ["a"], "environment": "prod"},
                {"name": "c", "version": "1.0", "depends_on": ["b"], "environment": "prod"},
            ],
        }
        result = assess(p)
        risk = {x["name"]: x["risk"] for x in result["components"]}
        self.assertEqual(risk, {"a": "low", "b": "medium", "c": "high"})
        self.assertEqual(result["deep_components"], ["c"])

    def test_patch_version_and_target_environment(self):
        p = dict(patch, environment="prod")
        result = assess(p)
        self.assertEqual(result["patch_version"], "2.9.1")
        self.assertEqual(result["target_environment"], "prod")

    def test_report_counts_and_excluded(self):
        p = {
            "name": "libxml2",
            "version": "2.9.1",
            "components": [
                {"name": "legacy", "version": "1.0", "depends_on": ["libxml2<2.0"],
                 "environment": "test"},
            ],
        }
        assess(p)
        result = report("libxml2")
        self.assertGreaterEqual(result["count"], 1)
        self.assertEqual([e["name"] for e in result["excluded"]], ["legacy"])

    def test_export_by_environment(self):
        result = assess(patch)
        prod = export(result, "prod")
        self.assertEqual(prod["environment"], "prod")
        self.assertEqual([c["name"] for c in prod["components"]], ["exporter", "portal"])
        all_envs = export(result)
        self.assertEqual(sorted(all_envs), ["prod", "test"])
        self.assertEqual([c["name"] for c in all_envs["test"]["components"]], ["report"])

    def test_diff_between_assessments(self):
        first = assess(patch)
        p2 = dict(patch, version="2.9.2")
        p2["components"] = patch["components"] + [
            {"name": "scanner", "version": "0.9", "depends_on": ["exporter"], "environment": "prod"}
        ]
        second = assess(p2)
        d = diff(first, second)
        self.assertEqual(d["from_version"], "2.9.1")
        self.assertEqual(d["to_version"], "2.9.2")
        self.assertEqual([c["name"] for c in d["added_components"]], ["scanner"])
        self.assertEqual(d["removed_components"], [])

    def test_reverse_lookup(self):
        assess(patch)
        hits = reverse_lookup("exporter")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["patch"], "libxml2")
        self.assertEqual(hits[0]["level"], 2)
        self.assertEqual(reverse_lookup("nothing"), [])


if __name__ == "__main__":
    unittest.main()
