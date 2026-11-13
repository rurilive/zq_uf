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
    def test_insert_select_sources(self):
        lineage = Lineage()
        item = lineage.record({
            "name": "job1",
            "sql": """
                INSERT INTO dws.user_daily
                SELECT o.user_id, u.region, sum(o.amount)
                FROM ods.orders_raw o
                JOIN ods.users u ON o.user_id = u.user_id
                GROUP BY o.user_id, u.region;
            """,
        })
        self.assertEqual(item["outputs"], ["dws.user_daily"])
        self.assertEqual(item["inputs"], ["ods.orders_raw", "ods.users"])

    def test_name_normalized_with_schema(self):
        lineage = Lineage()
        item = lineage.record({
            "name": "job1",
            "inputs": ["`ODS`.`Orders_Raw`"],
            "outputs": ["DWS.Clean"],
        })
        self.assertEqual(item["inputs"], ["ods.orders_raw"])
        self.assertEqual(item["outputs"], ["dws.clean"])

    def test_cte_and_subquery(self):
        lineage = Lineage()
        item = lineage.record({
            "name": "job1",
            "sql": """
                WITH recent AS (
                    SELECT * FROM ods.events WHERE dt = '2026-11-13'
                )
                INSERT INTO dws.recent_events
                SELECT r.*, u.region
                FROM recent r
                JOIN (SELECT user_id, region FROM ods.users) u
                  ON r.user_id = u.user_id;
            """,
        })
        self.assertEqual(item["inputs"], ["ods.events", "ods.users"])
        self.assertEqual(item["outputs"], ["dws.recent_events"])


class TempTableTest(unittest.TestCase):
    def test_temp_keyword_resolved(self):
        lineage = Lineage()
        item = lineage.record({
            "name": "job1",
            "sql": """
                CREATE TEMP TABLE tmp_daily AS
                SELECT user_id, sum(amount) AS total FROM ods.orders GROUP BY user_id;
                INSERT INTO dws.user_daily
                SELECT * FROM tmp_daily;
            """,
        })
        self.assertEqual(item["inputs"], ["ods.orders"])
        self.assertEqual(item["outputs"], ["dws.user_daily"])
        self.assertEqual(item["temps"], ["tmp_daily"])
        self.assertEqual(lineage.impact("tmp_daily"), [])
        self.assertEqual(lineage.impact("ods.orders"), ["job1"])

    def test_intermediate_table_resolved(self):
        lineage = Lineage()
        item = lineage.record({
            "name": "job1",
            "sql": """
                CREATE TABLE staging AS SELECT * FROM raw.a;
                INSERT INTO final.t SELECT * FROM staging;
            """,
        })
        self.assertEqual(item["inputs"], ["raw.a"])
        self.assertEqual(item["outputs"], ["final.t"])
        self.assertEqual(item["temps"], ["staging"])

    def test_declared_output_not_temp(self):
        lineage = Lineage()
        item = lineage.record({
            "name": "job1",
            "outputs": ["mid"],
            "sql": """
                CREATE TABLE mid AS SELECT * FROM raw.a;
                INSERT INTO final.t SELECT * FROM mid;
            """,
        })
        self.assertEqual(item["outputs"], ["mid", "final.t"])
        self.assertEqual(item["temps"], [])
        self.assertEqual(lineage.impact("mid"), ["job1"])


class RenameAliasTest(unittest.TestCase):
    def test_rename_keeps_alias(self):
        lineage = Lineage()
        lineage.record({"name": "job1", "inputs": ["raw"], "outputs": ["clean"]})
        lineage.rename("raw", "ods.raw_v2")
        self.assertEqual(lineage.impact("raw"), ["job1"])
        self.assertEqual(lineage.impact("ods.raw_v2"), ["job1"])
        self.assertIn("raw", lineage.nodes["ods.raw_v2"]["aliases"])

    def test_record_with_old_name_after_rename(self):
        lineage = Lineage()
        lineage.record({"name": "job1", "inputs": ["raw"], "outputs": ["clean"]})
        lineage.rename("raw", "ods.raw_v2")
        lineage.record({"name": "job2", "inputs": ["raw"], "outputs": ["mart"]})
        self.assertEqual(lineage.impact("raw"), ["job1", "job2"])
        srcs = {e["src"] for e in lineage.edges}
        self.assertNotIn("raw", srcs)

    def test_renames_in_job_config(self):
        lineage = Lineage()
        lineage.record({"name": "job1", "inputs": ["old_t"], "outputs": ["clean"]})
        lineage.record({
            "name": "job2",
            "renames": {"old_t": "new_t"},
            "inputs": ["new_t"],
            "outputs": ["mart"],
        })
        self.assertEqual(lineage.impact("old_t"), ["job1", "job2"])


class MultiHopTest(unittest.TestCase):
    def setUp(self):
        self.lineage = Lineage()
        self.lineage.record({"name": "job1", "inputs": ["raw"], "outputs": ["clean"]})
        self.lineage.record({"name": "job2", "inputs": ["clean"], "outputs": ["mart"]})
        self.lineage.record({"name": "job3", "inputs": ["mart"], "outputs": ["report"]})

    def test_downstream_multi_hop(self):
        self.assertEqual(self.lineage.impact("raw"), ["job1", "job2", "job3"])

    def test_downstream_levels(self):
        result = self.lineage.impact("raw")
        self.assertEqual(result.levels, [["job1"], ["job2"], ["job3"]])

    def test_upstream_direction(self):
        result = self.lineage.impact("report", direction="upstream")
        self.assertEqual(result, ["job3", "job2", "job1"])
        self.assertEqual(result.levels, [["job3"], ["job2"], ["job1"]])

    def test_mid_chain(self):
        self.assertEqual(self.lineage.impact("clean"), ["job2", "job3"])
