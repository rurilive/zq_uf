import os
import subprocess
import sys
import unittest

from fieldlineage import (JobFormatError, SQLParseError, UnknownFieldError,
                          fields, record, reset, trace, trace_field)


class FieldMappingTest(unittest.TestCase):
    def setUp(self):
        reset()
        record({"job": "j1", "inputs": ["raw.orders"], "outputs": ["dwd.orders"],
                "columns": [{"from": "raw.orders.amount", "to": "dwd.orders.total"}]})

    def test_upstream_field(self):
        result = trace_field("dwd.orders", "total")
        self.assertEqual([u["field"] for u in result["upstream"]], ["raw.orders.amount"])
        self.assertEqual(result["upstream"][0]["level"], 1)
        self.assertEqual(result["upstream"][0]["kind"], "direct")

    def test_downstream_field(self):
        result = trace_field("raw.orders", "amount")
        self.assertEqual([d["field"] for d in result["downstream"]], ["dwd.orders.total"])

    def test_trace_with_field_returns_path_jobs(self):
        self.assertEqual(len(trace("dwd.orders", "total")), 1)

    def test_fields_listing(self):
        self.assertEqual(fields("dwd.orders"), ["dwd.orders.total"])
        self.assertEqual(fields(), ["dwd.orders.total", "raw.orders.amount"])


class SqlAliasTest(unittest.TestCase):
    def setUp(self):
        reset()
        record({"job": "etl", "inputs": ["raw.orders"], "outputs": ["dwd.orders"],
                "sql": "insert into dwd.orders "
                       "select id, amount as total, amount * rate as cost "
                       "from raw.orders"})

    def test_alias_edge(self):
        result = trace_field("dwd.orders", "total")
        self.assertEqual([u["field"] for u in result["upstream"]], ["raw.orders.amount"])
        self.assertEqual(result["kind"], "direct")

    def test_expression_edge(self):
        result = trace_field("dwd.orders", "cost")
        self.assertEqual([u["field"] for u in result["upstream"]],
                         ["raw.orders.amount", "raw.orders.rate"])
        self.assertEqual(result["kind"], "expression")
        self.assertEqual(result["expression"], "amount * rate")

    def test_lone_column_keeps_name(self):
        result = trace_field("dwd.orders", "id")
        self.assertEqual([u["field"] for u in result["upstream"]], ["raw.orders.id"])


class AggregationTest(unittest.TestCase):
    def setUp(self):
        reset()
        record({"job": "agg", "inputs": ["raw.orders"], "outputs": ["dwd.daily"],
                "sql": "insert into dwd.daily "
                       "select user_id, sum(amount) as total_amt, count(*) as cnt "
                       "from raw.orders group by user_id"})

    def test_aggregate_function_recorded(self):
        result = trace_field("dwd.daily", "total_amt")
        self.assertEqual(result["kind"], "aggregation")
        self.assertEqual(result["functions"], ["SUM"])
        self.assertEqual([u["field"] for u in result["upstream"]], ["raw.orders.amount"])

    def test_count_star_keeps_attrs_without_upstream(self):
        result = trace_field("dwd.daily", "cnt")
        self.assertEqual(result["upstream"], [])
        self.assertEqual(result["functions"], ["COUNT"])
        self.assertEqual(result["kind"], "aggregation")


class JoinTest(unittest.TestCase):
    def setUp(self):
        reset()
        record({"job": "j", "inputs": ["raw.orders", "raw.users"], "outputs": ["dwd.fact"],
                "sql": "insert into dwd.fact "
                       "select o.id as order_id, u.name as user_name, "
                       "o.amount * u.discount as cost "
                       "from raw.orders o join raw.users u on o.uid = u.id"})

    def test_join_expression_edge(self):
        result = trace_field("dwd.fact", "cost")
        self.assertEqual([u["field"] for u in result["upstream"]],
                         ["raw.orders.amount", "raw.users.discount"])

    def test_join_qualified_column(self):
        result = trace_field("dwd.fact", "user_name")
        self.assertEqual([u["field"] for u in result["upstream"]], ["raw.users.name"])


class MultiHopTest(unittest.TestCase):
    def setUp(self):
        reset()
        record({"job": "j1", "inputs": ["raw.orders"], "outputs": ["dwd.orders"],
                "sql": "insert into dwd.orders "
                       "select user_id, sum(amount) as total_amt "
                       "from raw.orders group by user_id"})
        record({"job": "j2", "inputs": ["dwd.orders"], "outputs": ["dm.stats"],
                "sql": "insert into dm.stats "
                       "select user_id as uid, total_amt as lv from dwd.orders"})

    def test_two_hop_upstream_levels(self):
        result = trace_field("dm.stats", "lv")
        ups = {u["field"]: u["level"] for u in result["upstream"]}
        self.assertEqual(ups["dwd.orders.total_amt"], 1)
        self.assertEqual(ups["raw.orders.amount"], 2)

    def test_two_hop_downstream_levels(self):
        result = trace_field("raw.orders", "amount")
        downs = {d["field"]: d["level"] for d in result["downstream"]}
        self.assertEqual(downs["dwd.orders.total_amt"], 1)
        self.assertEqual(downs["dm.stats.lv"], 2)

    def test_max_depth_limits_levels(self):
        result = trace_field("dm.stats", "lv", max_depth=1)
        self.assertEqual([u["field"] for u in result["upstream"]], ["dwd.orders.total_amt"])

    def test_max_depth_zero(self):
        result = trace_field("dm.stats", "lv", max_depth=0)
        self.assertEqual(result["upstream"], [])
        self.assertEqual(result["downstream"], [])

    def test_trace_field_unknown_raises(self):
        with self.assertRaises(UnknownFieldError):
            trace_field("dm.stats", "nope")

    def test_max_depth_bad_type(self):
        with self.assertRaises(TypeError):
            trace_field("dm.stats", "lv", max_depth="2")

    def test_max_depth_negative(self):
        with self.assertRaises(ValueError):
            trace_field("dm.stats", "lv", max_depth=-1)


class RenameTest(unittest.TestCase):
    def setUp(self):
        reset()
        record({"job": "j1", "inputs": ["raw.orders"], "outputs": ["dwd.orders"],
                "columns": [{"from": "raw.orders.amount", "to": "dwd.orders.total"}]})
        record({"job": "j2", "inputs": [], "outputs": [],
                "renames": [{"from": "dwd.orders.total", "to": "dwd.orders.gross_total"}]})

    def test_old_name_resolves_to_new(self):
        result = trace_field("dwd.orders", "total")
        self.assertEqual(result["field"], "dwd.orders.gross_total")
        self.assertEqual(result["aliases"], ["dwd.orders.total"])
        ups = {u["field"]: u["level"] for u in result["upstream"]}
        self.assertEqual(ups["dwd.orders.total"], 1)
        self.assertEqual(ups["raw.orders.amount"], 2)

    def test_trace_by_old_name_still_finds_jobs(self):
        self.assertEqual(trace("dwd.orders", "total"), trace("dwd.orders", "gross_total"))
        self.assertEqual(len(trace("dwd.orders", "total")), 2)

    def test_rename_unknown_field_raises(self):
        with self.assertRaises(JobFormatError):
            record({"job": "j3", "renames": [{"from": "dwd.orders.ghost", "to": "dwd.orders.g2"}]})

    def test_rename_to_existing_raises(self):
        with self.assertRaises(JobFormatError):
            record({"job": "j3", "renames": [{"from": "dwd.orders.gross_total",
                                              "to": "dwd.orders.gross_total"}]})

    def test_rename_across_tables_raises(self):
        with self.assertRaises(JobFormatError):
            record({"job": "j3", "renames": [{"from": "dwd.orders.gross_total",
                                              "to": "dm.other.gross_total"}]})


class RecordValidationTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_job_not_dict(self):
        with self.assertRaises(TypeError):
            record("not-a-dict")

    def test_missing_job_name(self):
        with self.assertRaises(JobFormatError):
            record({"inputs": [], "outputs": []})

    def test_unknown_job_key(self):
        with self.assertRaises(JobFormatError):
            record({"job": "j", "column": []})

    def test_bad_inputs_type(self):
        with self.assertRaises(JobFormatError):
            record({"job": "j", "inputs": "raw.orders", "outputs": []})

    def test_bad_column_format(self):
        with self.assertRaises(JobFormatError):
            record({"job": "j", "inputs": ["a"], "outputs": ["b"],
                    "columns": [{"from": "noDot", "to": "b.x"}]})

    def test_column_from_table_not_in_inputs(self):
        with self.assertRaises(JobFormatError):
            record({"job": "j", "inputs": ["a"], "outputs": ["b"],
                    "columns": [{"from": "c.x", "to": "b.y"}]})

    def test_column_to_table_not_in_outputs(self):
        with self.assertRaises(JobFormatError):
            record({"job": "j", "inputs": ["a"], "outputs": ["b"],
                    "columns": [{"from": "a.x", "to": "c.y"}]})


class SqlParseFailureTest(unittest.TestCase):
    def setUp(self):
        reset()

    def _record_sql(self, sql, inputs=("raw.orders",), outputs=("dwd.t",)):
        record({"job": "j", "inputs": list(inputs), "outputs": list(outputs), "sql": sql})

    def test_select_star(self):
        with self.assertRaises(SQLParseError):
            self._record_sql("insert into dwd.t select * from raw.orders")

    def test_table_star(self):
        with self.assertRaises(SQLParseError):
            self._record_sql("insert into dwd.t select o.* from raw.orders o")

    def test_subquery(self):
        with self.assertRaises(SQLParseError):
            self._record_sql("insert into dwd.t select x from (select x from raw.orders) t")

    def test_union(self):
        with self.assertRaises(SQLParseError):
            self._record_sql("insert into dwd.t select x from raw.orders "
                             "union select x from raw.orders")

    def test_missing_from(self):
        with self.assertRaises(SQLParseError):
            self._record_sql("insert into dwd.t select 1")

    def test_not_insert_select(self):
        with self.assertRaises(SQLParseError):
            self._record_sql("delete from raw.orders")

    def test_empty_select_list(self):
        with self.assertRaises(SQLParseError):
            self._record_sql("insert into dwd.t select from raw.orders")

    def test_expression_without_alias(self):
        with self.assertRaises(JobFormatError):
            self._record_sql("insert into dwd.t select amount * 2 from raw.orders")

    def test_target_not_in_outputs(self):
        with self.assertRaises(JobFormatError):
            self._record_sql("insert into other.t select x from raw.orders")

    def test_source_not_in_inputs(self):
        with self.assertRaises(JobFormatError):
            self._record_sql("insert into dwd.t select x from raw.ghost")

    def test_ambiguous_bare_column(self):
        with self.assertRaises(SQLParseError):
            record({"job": "j", "inputs": ["a.t1", "a.t2"], "outputs": ["b.t"],
                    "sql": "insert into b.t select id from a.t1 join a.t2 on a.t1.id = a.t2.id"})

    def test_unknown_qualifier(self):
        with self.assertRaises(SQLParseError):
            self._record_sql("insert into dwd.t select z.amount from raw.orders")


class DemoTest(unittest.TestCase):
    def test_demo_output_matches_trace_field(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run([sys.executable, "demo.py"], cwd=root,
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("'field': 'dm.user_stats.ltv'", out)
        self.assertIn("'aliases': ['dm.user_stats.lifetime_value']", out)
        self.assertIn("'field': 'raw.orders.amount', 'level': 3", out)
        self.assertIn("'functions': ['SUM']", out)
        last_line = out.strip().splitlines()[-1]
        self.assertEqual(last_line.count("'job':"), 3)


if __name__ == "__main__":
    unittest.main()

