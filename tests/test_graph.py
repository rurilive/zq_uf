import unittest

from reachable import reachable, report


def make_deps(**overrides):
    deps = {
        "project": "shop",
        "modules": [
            {"name": "log4j", "version": "2.14.1"},
            {"name": "commons-io", "version": "1.2"},
            {"name": "groovy", "version": "3.0.9"},
            {"name": "snakeyaml", "version": "1.29"},
        ],
        "vulnerable": [
            {"name": "log4j", "version": "2.14.1"},
            {"name": "commons-io", "version": "1.2.0"},  # 写法不同，归一后命中
            {"name": "groovy", "version": "3.0.9"},
            {"name": "snakeyaml", "version": "1.29"},
        ],
        "entries": [
            {"name": "shop.web.main", "source": "http"},
            {"name": "shop.batch.sync", "source": "cron"},
        ],
        "calls": [
            {"from": "shop.web.main", "to": "shop.web.handler"},
            {"from": "shop.web.handler", "to": "log4j"},
            {"from": "shop.batch.sync", "to": "shop.web.handler"},
            {"from": "shop.web.handler", "to": "commons-io"},
        ],
        "dynamic": [{"component": "groovy", "via": "reflection"}],
    }
    deps.update(overrides)
    return deps


class ReachableTest(unittest.TestCase):
    def test_reachable_has_path_and_entry(self):
        result = reachable(make_deps())
        hit = next(h for h in result["reachable"] if h["component"] == "log4j")
        paths = hit["paths"]
        self.assertTrue(paths)
        for p in paths:
            self.assertEqual(p["path"][0], p["entry"])
            self.assertEqual(p["path"][-1], "log4j")
            self.assertIsNotNone(p["source"])

    def test_multi_entry_merged_into_one_record(self):
        result = reachable(make_deps())
        hits = [h for h in result["reachable"] if h["component"] == "log4j"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            sorted(hits[0]["entries"]), ["shop.batch.sync", "shop.web.main"]
        )
        self.assertEqual(len(hits[0]["paths"]), 2)

    def test_version_format_mismatch_normalized(self):
        result = reachable(make_deps())
        names = [h["component"] for h in result["reachable"]]
        self.assertIn("commons-io", names)
        hit = next(h for h in result["reachable"] if h["component"] == "commons-io")
        self.assertEqual(hit["version"], "1.2")  # 保留组件实际版本
        self.assertEqual(hit["match"]["basis"], "normalized")
        self.assertEqual(hit["match"]["vuln_version"], "1.2.0")

    def test_unreachable_has_reason(self):
        result = reachable(make_deps())
        item = next(u for u in result["unreachable"] if u["component"] == "snakeyaml")
        self.assertEqual(item["reason"], "未被任何代码调用")

    def test_unreachable_but_called_off_entry(self):
        deps = make_deps()
        deps["calls"].append({"from": "shop.internal.helper", "to": "snakeyaml"})
        result = reachable(deps)
        item = next(u for u in result["unreachable"] if u["component"] == "snakeyaml")
        self.assertIn("不连通", item["reason"])

    def test_dynamic_loading_is_pending(self):
        result = reachable(make_deps())
        self.assertEqual([p["component"] for p in result["pending"]], ["groovy"])
        self.assertEqual(result["pending"][0]["via"], "reflection")
        self.assertNotIn(
            "groovy", [h["component"] for h in result["reachable"]]
        )

    def test_dynamic_reachable_statically_stays_reachable(self):
        deps = make_deps()
        deps["calls"].append({"from": "shop.web.main", "to": "groovy"})
        result = reachable(deps)
        self.assertIn("groovy", [h["component"] for h in result["reachable"]])
        self.assertEqual(result["pending"], [])

    def test_exact_match_basis(self):
        result = reachable(make_deps())
        hit = next(h for h in result["reachable"] if h["component"] == "log4j")
        self.assertEqual(hit["match"]["basis"], "exact")

    def test_version_prefix_normalized(self):
        deps = make_deps()
        deps["modules"][0]["version"] = "v2.14.1"
        result = reachable(deps)
        hit = next(h for h in result["reachable"] if h["component"] == "log4j")
        self.assertEqual(hit["match"]["basis"], "normalized")

    def test_report_aggregates_by_project(self):
        reachable(make_deps())
        rep = report("shop")
        self.assertGreaterEqual(rep["count"], 1)
        self.assertTrue(rep["reachable"])
        self.assertTrue(rep["unreachable"])
        self.assertIn("groovy", [p["component"] for p in rep["pending"]])
        other = report("other-project")
        self.assertEqual(other["count"], 0)
        self.assertEqual(other["reachable"], [])

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            reachable(["not", "a", "dict"])


if __name__ == "__main__":
    unittest.main()
