import unittest

from patchimpact import assess, diff, find_by_component, report

patch = {
    "name": "libxml2",
    "version": "2.9.1",
    "environment": "prod",
    "version_ranges": {"report": ">=1.0,<2.0", "legacy": ">=2.0"},
    "components": [
        {"name": "portal", "version": "3.0", "depends_on": ["libxml2"], "environment": "prod"},
        {"name": "report", "version": "1.4", "depends_on": ["libxml2"], "environment": "test"},
        {"name": "exporter", "version": "2.1", "depends_on": ["report"], "environment": "prod"},
        {"name": "archiver", "version": "0.9", "depends_on": ["exporter"], "environment": "prod"},
        {"name": "legacy", "version": "1.0", "depends_on": ["libxml2"], "environment": "test"},
    ],
}


class ImpactTest(unittest.TestCase):
    def test_direct_and_indirect_dependents(self):
        names = [c["name"] for c in assess(patch)["components"]]
        self.assertEqual(names, ["archiver", "exporter", "portal", "report"])

    def test_dependency_levels(self):
        result = assess(patch)
        levels = {c["name"]: c["level"] for c in result["components"]}
        self.assertEqual(levels, {"portal": 1, "report": 1, "exporter": 2, "archiver": 3})
        self.assertEqual(result["max_level"], 3)

    def test_deep_level_risk_flagged(self):
        result = assess(patch)
        self.assertEqual(result["deep_components"], ["archiver"])
        archiver = next(c for c in result["components"] if c["name"] == "archiver")
        self.assertTrue(archiver["deep"])
        self.assertTrue(archiver["risk"])

    def test_version_range_exclusion_with_reason(self):
        result = assess(patch)
        self.assertEqual([e["name"] for e in result["excluded"]], ["legacy"])
        self.assertIn("不在补丁适用范围", result["excluded"][0]["reason"])

    def test_result_carries_patch_version_and_target_environment(self):
        result = assess(patch)
        self.assertEqual(result["patch_version"], "2.9.1")
        self.assertEqual(result["target_environment"], "prod")

    def test_environments_merged(self):
        self.assertEqual(assess(patch)["environments"], ["prod", "test"])

    def test_report_counts_and_excluded(self):
        assess(patch)
        result = report("libxml2")
        self.assertGreaterEqual(result["count"], 1)
        self.assertEqual({e["name"] for e in result["excluded"]}, {"legacy"})

    def test_report_export_per_environment(self):
        assess(patch)
        prod = report("libxml2", environment="prod")
        envs = {c["environment"] for row in prod["assessments"] for c in row["components"]}
        self.assertEqual(envs, {"prod"})
        self.assertEqual(prod["excluded"], [])  # legacy 在 test 环境，被过滤掉

    def test_diff_between_assessments(self):
        first = assess(patch)
        changed_patch = dict(
            patch,
            components=[c for c in patch["components"] if c["name"] != "exporter"],
        )
        second = assess(changed_patch)
        result = diff(first, second)
        self.assertEqual(result["removed"], ["archiver", "exporter"])
        self.assertEqual(result["added"], [])
        self.assertEqual(result["excluded_added"], [])
        self.assertEqual(result["excluded_removed"], [])

    def test_find_by_component(self):
        assess(patch)
        hits = find_by_component("exporter")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["patch"], "libxml2")
        self.assertEqual(hits[0]["level"], 2)
        self.assertEqual(find_by_component("not-exist"), [])


if __name__ == "__main__":
    unittest.main()
