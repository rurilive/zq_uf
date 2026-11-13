import json
import os
import subprocess
import sys
import tempfile
import unittest

from pipelineview import ViewError, build, export, impact, load, upstream


JOBS = [
    {"name": "extract"},
    {"name": "clean", "depends": ["extract"]},
    {"name": "report", "depends": ["clean"]},
]


class ViewTest(unittest.TestCase):
    def setUp(self):
        build(JOBS)

    def test_task_level_nodes(self):
        self.assertEqual(build(JOBS)["nodes"], ["extract", "clean", "report"])

    def test_direct_dependents_only(self):
        self.assertEqual(impact("extract"), ["clean"])

    def test_no_field_nodes(self):
        self.assertTrue(all(isinstance(node, str) for node in build(JOBS)["nodes"]))


PIPE_JOBS = [
    {"name": "extract",
     "writes": [{"table": "raw_orders", "fields": ["id", "amount"]}]},
    {"name": "clean", "depends": ["extract"],
     "reads": [{"table": "raw_orders", "fields": ["id", "amount"]}],
     "writes": [{"table": "orders", "fields": ["id", "amount"]}]},
    {"name": "report",
     "reads": [{"table": "orders", "fields": ["id", "amount"]}]},
]

PIPE_RUNS = [
    {"job": "extract", "writes": ["raw_orders"]},
    {"job": "clean", "reads": ["raw_orders"], "writes": ["orders"]},
    {"job": "report", "reads": ["orders"]},
]


class InferTest(unittest.TestCase):
    def test_inferred_edge_from_config_and_runs(self):
        view = build(PIPE_JOBS, runs=PIPE_RUNS)
        self.assertEqual(view["inferred"], [("clean", "report")])
        self.assertIn(("clean", "report"), view["edges"])

    def test_min_runs_threshold_blocks_inference(self):
        view = build(PIPE_JOBS, runs=PIPE_RUNS, min_runs=3)
        self.assertEqual(view["inferred"], [])
        self.assertNotIn(("clean", "report"), view["edges"])

    def test_run_only_inference_without_config_io(self):
        jobs = [{"name": "a"}, {"name": "b"}]
        runs = [{"job": "a", "writes": ["t"]}, {"job": "b", "reads": ["t"]}]
        self.assertEqual(build(jobs, runs=runs)["inferred"], [("a", "b")])

    def test_config_edge_not_duplicated_as_inferred(self):
        view = build(PIPE_JOBS, runs=PIPE_RUNS)
        self.assertEqual(view["edges"].count(("extract", "clean")), 1)


class FieldLevelTest(unittest.TestCase):
    def setUp(self):
        build(PIPE_JOBS, runs=PIPE_RUNS)

    def test_table_and_field_nodes(self):
        view = build(PIPE_JOBS, runs=PIPE_RUNS)
        self.assertEqual(view["tables"], ["raw_orders", "orders"])
        self.assertEqual(
            view["fields"],
            ["raw_orders.id", "raw_orders.amount", "orders.id", "orders.amount"],
        )

    def test_field_downstream(self):
        self.assertEqual(
            impact("raw_orders.amount", depth=None),
            ["clean", "orders", "orders.amount", "orders.id", "report"],
        )

    def test_table_downstream_direct(self):
        self.assertEqual(impact("orders"), ["report"])

    def test_task_transitive_downstream(self):
        self.assertEqual(
            impact("extract", depth=None),
            ["clean", "orders", "orders.amount", "orders.id",
             "raw_orders", "raw_orders.amount", "raw_orders.id", "report"],
        )

    def test_upstream_query(self):
        self.assertEqual(
            upstream("report", depth=None),
            ["clean", "extract", "orders", "orders.amount", "orders.id",
             "raw_orders", "raw_orders.amount", "raw_orders.id"],
        )

    def test_upstream_direct(self):
        self.assertEqual(
            upstream("report"),
            ["clean", "orders", "orders.amount", "orders.id"],
        )


class ImpactPathTest(unittest.TestCase):
    def setUp(self):
        build(PIPE_JOBS, runs=PIPE_RUNS)

    def test_impact_paths_and_levels(self):
        result = impact("extract", depth=2, paths=True)
        self.assertEqual(
            result["levels"],
            {"clean": 1, "raw_orders": 1, "raw_orders.amount": 1,
             "raw_orders.id": 1, "orders": 2, "orders.amount": 2,
             "orders.id": 2, "report": 2},
        )
        self.assertEqual(
            result["paths"],
            [["extract", "clean"],
             ["extract", "clean", "orders"],
             ["extract", "clean", "orders.amount"],
             ["extract", "clean", "orders.id"],
             ["extract", "clean", "report"],
             ["extract", "raw_orders"],
             ["extract", "raw_orders", "clean"],
             ["extract", "raw_orders.amount"],
             ["extract", "raw_orders.amount", "clean"],
             ["extract", "raw_orders.id"],
             ["extract", "raw_orders.id", "clean"]],
        )

    def test_cycle_does_not_hang(self):
        jobs = [{"name": "a", "depends": ["b"]}, {"name": "b", "depends": ["a"]}]
        build(jobs)
        self.assertEqual(impact("a", depth=None), ["b"])


class ExportTest(unittest.TestCase):
    def setUp(self):
        build(PIPE_JOBS, runs=PIPE_RUNS)

    def test_export_is_json_serializable(self):
        json.dumps(export())

    def test_load_roundtrip_restores_queries(self):
        before = impact("extract", depth=None)
        load(export())
        self.assertEqual(impact("extract", depth=None), before)
        self.assertEqual(upstream("report"), ["clean", "orders", "orders.amount", "orders.id"])

    def test_export_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "view.json")
            data = export(path)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), data)


class ErrorTest(unittest.TestCase):
    def test_unknown_node_raises(self):
        build(JOBS)
        with self.assertRaises(ViewError):
            impact("ghost")

    def test_unknown_dependency_raises(self):
        with self.assertRaises(ViewError):
            build([{"name": "a", "depends": ["ghost"]}])

    def test_unknown_run_job_raises(self):
        with self.assertRaises(ViewError):
            build([{"name": "a"}], runs=[{"job": "ghost", "reads": ["t"]}])

    def test_duplicate_job_raises(self):
        with self.assertRaises(ViewError):
            build([{"name": "a"}, {"name": "a"}])

    def test_table_task_name_conflict_raises(self):
        with self.assertRaises(ViewError):
            build([{"name": "a", "writes": ["a"]}])

    def test_malformed_io_raises(self):
        with self.assertRaises(ViewError):
            build([{"name": "a", "reads": [42]}])

    def test_empty_jobs_raises(self):
        with self.assertRaises(ViewError):
            build([])

    def test_bad_depth_raises(self):
        build(JOBS)
        with self.assertRaises(ViewError):
            impact("extract", depth=0)

    def test_load_rejects_bad_data(self):
        with self.assertRaises(ViewError):
            load({"nodes": []})
        build(PIPE_JOBS, runs=PIPE_RUNS)
        with self.assertRaises(ViewError):
            data = export()
            data["edges"] = [["ghost", "nowhere"]]
            load(data)


class DemoTest(unittest.TestCase):
    def test_demo_output_matches_assertions(self):
        out = subprocess.run(
            [sys.executable, "demo.py"],
            capture_output=True, text=True, check=True,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
        ).stdout
        self.assertIn("inferred: [('clean', 'report')]", out)
        self.assertIn(
            "impact(raw_orders.amount): "
            "['clean', 'orders', 'orders.amount', 'orders.id', 'report']",
            out,
        )
        self.assertIn(
            "restored: "
            "['clean', 'orders', 'orders.amount', 'orders.id', "
            "'raw_orders', 'raw_orders.amount', 'raw_orders.id', 'report']",
            out,
        )
