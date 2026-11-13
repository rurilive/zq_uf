import unittest

from lineage import Lineage


class LineageTest(unittest.TestCase):
    def test_record(self):
        lineage = Lineage()
        item = lineage.record({"name": "job1", "inputs": ["raw"], "outputs": ["clean"]})
        self.assertEqual(item["outputs"], ["clean"])

    def test_impact_direct(self):
        lineage = Lineage()
        lineage.record({"name": "job1", "inputs": ["raw"], "outputs": ["clean"]})
        self.assertEqual(lineage.impact("raw"), ["job1"])

    def test_impact_unknown(self):
        self.assertEqual(Lineage().impact("nope"), [])


class SqlParseTest(unittest.TestCase):
    def test_parse_insert_select(self):
        item = Lineage().record({"name": "etl", "sql":
            "INSERT INTO dwd.clean SELECT a.id FROM app.raw a JOIN app.dim d ON a.k = d.k"})
        self.assertEqual(item["inputs"], ["app.raw", "app.dim"])
        self.assertEqual(item["outputs"], ["dwd.clean"])

    def test_parse_cte_is_temp(self):
        item = Lineage().record({"name": "etl", "sql":
            "WITH x AS (SELECT * FROM raw) INSERT INTO final SELECT * FROM x"})
        self.assertEqual(item["inputs"], ["raw"])
        self.assertEqual(item["outputs"], ["final"])
        self.assertEqual(item["temps"], ["x"])

    def test_name_normalized(self):
        item = Lineage().record({"name": "etl", "sql":
            "INSERT INTO `DWD`.`Clean` SELECT * FROM APP.Raw"})
        self.assertEqual(item["inputs"], ["app.raw"])
        self.assertEqual(item["outputs"], ["dwd.clean"])


class TempTableTest(unittest.TestCase):
    def test_temp_resolved_inside_job(self):
        lineage = Lineage()
        item = lineage.record({"name": "etl", "sql":
            "CREATE TEMP TABLE tmp AS SELECT * FROM raw;"
            "INSERT INTO final SELECT * FROM tmp;"})
        self.assertEqual(item["inputs"], ["raw"])
        self.assertEqual(item["outputs"], ["final"])
        self.assertIn("tmp", item["temps"])
        self.assertEqual(lineage.impact("tmp"), [])
        for edge in lineage.edges:
            self.assertNotIn("tmp", (edge["source"], edge["target"]))


class RenameAliasTest(unittest.TestCase):
    def test_rename_keeps_lineage(self):
        lineage = Lineage()
        lineage.record({"name": "job1", "inputs": ["raw"], "outputs": ["clean"]})
        lineage.rename("clean", "clean_v2")
        lineage.record({"name": "job2", "inputs": ["clean_v2"], "outputs": ["mart"]})
        self.assertEqual(lineage.impact("raw"), ["job1", "job2"])
        self.assertEqual(lineage.impact("clean"), ["job2"])  # 旧名仍可查

    def test_rename_via_job_config(self):
        lineage = Lineage()
        lineage.record({"name": "job1", "inputs": ["raw"], "outputs": ["clean"]})
        lineage.record({"name": "job2", "inputs": ["clean2"], "outputs": ["mart"],
                        "renames": {"clean": "clean2"}})
        self.assertEqual(lineage.impact("raw"), ["job1", "job2"])


class MultiHopTest(unittest.TestCase):
    def _chain(self):
        lineage = Lineage()
        lineage.record({"name": "job1", "inputs": ["a"], "outputs": ["b"]})
        lineage.record({"name": "job2", "inputs": ["b"], "outputs": ["c"]})
        lineage.record({"name": "job3", "inputs": ["c"], "outputs": ["d"]})
        return lineage

    def test_downstream_levels(self):
        result = self._chain().impact("a")
        self.assertEqual(result, ["job1", "job2", "job3"])
        self.assertEqual(result.levels, {"job1": 1, "job2": 2, "job3": 3})

    def test_upstream_levels(self):
        result = self._chain().impact("d", direction="upstream")
        self.assertEqual(result, ["job3", "job2", "job1"])
        self.assertEqual(result.levels["job1"], 3)
