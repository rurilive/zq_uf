import json
import os
import tempfile
import unittest

from filelineage import (
    downstream,
    export_graph,
    impact,
    job_lineage,
    lineage_path,
    record,
    reset,
    upstream,
)


class LineageTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_record_counts_reads(self):
        self.assertEqual(record({"id": "j1", "reads": ["a.csv"], "writes": ["b.csv"]})["reads"], 1)

    def test_record_builds_no_edges(self):
        self.assertEqual(record({"id": "j2", "reads": ["c.csv"], "writes": []})["edges"], 0)

    def test_impact_is_one_hop(self):
        record({"id": "j3", "reads": ["d.csv"], "writes": ["e.csv"]})
        self.assertEqual(impact("d.csv")["depth"], 1)

    def test_record_builds_file_edges(self):
        result = record({"id": "j4", "reads": ["raw.csv"], "writes": ["clean.csv"]})
        self.assertEqual(result["edges"], 1)
        self.assertEqual(result["path"], [["raw.csv", "clean.csv"]])
        graph = export_graph()
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(graph["edges"][0]["source"], "raw.csv")
        self.assertEqual(graph["edges"][0]["target"], "clean.csv")

    def test_file_edge_records_modes_and_batch(self):
        record({
            "id": "j5",
            "batch": "2026-11-15",
            "reads": [{"path": "in.csv", "mode": "rb"}],
            "writes": [{"path": "out.csv", "mode": "w"}],
        })
        graph = export_graph()
        edge = graph["edges"][0]
        self.assertEqual(edge["batch"], "2026-11-15")
        self.assertEqual(edge["read_modes"], ["rb"])
        self.assertEqual(edge["write_modes"], ["w"])
        self.assertEqual(edge["jobs"], ["j5"])

    def test_multi_hop_has_levels_and_paths(self):
        record({"id": "s1", "reads": ["raw.csv"], "writes": ["clean.csv"]})
        record({"id": "s2", "reads": ["clean.csv"], "writes": ["report.csv"]})
        record({"id": "s3", "reads": ["report.csv"], "writes": ["dashboard.csv"]})
        result = impact("raw.csv")
        self.assertEqual(result["depth"], 3)
        self.assertEqual(
            sorted((node["path"], node["depth"]) for node in result["nodes"]),
            [("clean.csv", 1), ("dashboard.csv", 3), ("report.csv", 2)],
        )
        self.assertIn(["raw.csv", "clean.csv", "report.csv", "dashboard.csv"], result["paths"])

    def test_impact_upstream_direction(self):
        record({"id": "u1", "reads": ["raw.csv"], "writes": ["clean.csv"]})
        record({"id": "u2", "reads": ["clean.csv"], "writes": ["report.csv"]})
        result = upstream("report.csv")
        self.assertEqual(result["depth"], 2)
        self.assertEqual(result["jobs"], ["u2"])
        self.assertEqual(
            sorted(node["path"] for node in result["upstream"]),
            ["clean.csv", "raw.csv"],
        )
        self.assertEqual(downstream("report.csv")["nodes"], [])

    def test_multi_hop_results_are_deduplicated(self):
        # 两条路径汇聚到同一个下游文件，只应出现一次。
        record({"id": "d1", "reads": ["a.csv"], "writes": ["b.csv"]})
        record({"id": "d2", "reads": ["a.csv"], "writes": ["c.csv"]})
        record({"id": "d3", "reads": ["b.csv", "c.csv"], "writes": ["d.csv"]})
        result = impact("a.csv")
        self.assertEqual([node["path"] for node in result["nodes"]], ["b.csv", "c.csv", "d.csv"])
        self.assertEqual([node["path"] for node in result["nodes"]].count("d.csv"), 1)
        self.assertEqual(result["depth"], 2)

    def test_cycle_is_truncated_and_keeps_entry_node(self):
        record({"id": "c1", "reads": ["a.csv"], "writes": ["b.csv"]})
        record({"id": "c2", "reads": ["b.csv"], "writes": ["c.csv"]})
        record({"id": "c3", "reads": ["c.csv"], "writes": ["a.csv"]})
        result = impact("a.csv", direction="downstream")
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["cycles"]), 1)
        self.assertEqual(result["cycles"][0]["node"], "a.csv")
        self.assertTrue(result["cycles"][0]["entry"])
        self.assertEqual(
            result["cycles"][0]["path"],
            ["a.csv", "b.csv", "c.csv", "a.csv"],
        )
        # 入口节点在环路闭合时保留，且不会被无限展开。
        entry_nodes = [node for node in result["nodes"] if node["path"] == "a.csv"]
        self.assertEqual(len(entry_nodes), 1)
        self.assertTrue(entry_nodes[0]["cycle"])

    def test_max_depth_truncates_and_reports_truncated(self):
        record({"id": "l1", "reads": ["a.csv"], "writes": ["b.csv"]})
        record({"id": "l2", "reads": ["b.csv"], "writes": ["c.csv"]})
        result = impact("a.csv", max_depth=1)
        self.assertEqual(result["depth"], 1)
        self.assertTrue(result["truncated"])
        self.assertEqual([node["path"] for node in result["nodes"]], ["b.csv"])

    def test_breakpoint_when_upstream_chain_is_broken(self):
        # b.csv 有下游但没有生产任务，向上追溯时断在 b.csv。
        record({"id": "b1", "reads": ["b.csv"], "writes": ["c.csv"]})
        result = impact("c.csv", direction="upstream")
        self.assertEqual([item["path"] for item in result["breakpoints"]], ["b.csv"])
        traced = lineage_path("missing.csv", "c.csv")
        self.assertFalse(traced["connected"])
        self.assertEqual(
            [item["path"] for item in traced["breakpoints"]],
            ["missing.csv"],
        )

    def test_lineage_path_returns_chain(self):
        record({"id": "p1", "reads": ["a.csv"], "writes": ["b.csv"]})
        record({"id": "p2", "reads": ["b.csv"], "writes": ["c.csv"]})
        traced = lineage_path("a.csv", "c.csv")
        self.assertTrue(traced["connected"])
        self.assertEqual(traced["path"], ["a.csv", "b.csv", "c.csv"])
        self.assertEqual(traced["depth"], 2)

    def test_directory_filter(self):
        record({"id": "f1", "reads": ["raw/in.csv"], "writes": ["stage/clean.csv"]})
        record({"id": "f2", "reads": ["stage/clean.csv"], "writes": ["out/report.csv"]})
        result = impact("raw/in.csv", under="stage")
        self.assertEqual([node["path"] for node in result["nodes"]], ["stage/clean.csv"])
        self.assertEqual(result["paths"], [["raw/in.csv", "stage/clean.csv"]])
        result = impact("raw/in.csv", dir="out")
        self.assertEqual([node["path"] for node in result["nodes"]], ["out/report.csv"])
        self.assertEqual(result["depth"], 2)

    def test_rename_does_not_duplicate_nodes(self):
        record({"id": "r0", "reads": ["v1.csv"], "writes": ["v2.csv"]})
        record({"id": "r1", "reads": ["v2.csv"], "writes": ["v3.csv"],
                "renames": [{"from": "v2.csv", "to": "v2_final.csv"}]})
        graph = export_graph()
        paths = [node["path"] for node in graph["nodes"]]
        self.assertNotIn("v2.csv", paths)
        self.assertIn("v2_final.csv", paths)
        self.assertEqual(graph["aliases"], {"v2.csv": "v2_final.csv"})
        alias_node = next(node for node in graph["nodes"] if node["path"] == "v2_final.csv")
        self.assertEqual(alias_node["aliases"], ["v2.csv"])
        self.assertEqual(len(set(paths)), len(paths))
        # 改名不产生自环派生边：v2_final.csv -> v3.csv 仍保留，v2_final -> v2_final 不应出现。
        for edge in graph["edges"]:
            self.assertNotEqual(edge["source"], edge["target"])
        result = impact("v1.csv")
        self.assertIn("v2_final.csv", [node["path"] for node in result["nodes"]])

    def test_rename_write_mode_without_explicit_from(self):
        record({"id": "rm1", "reads": ["old.csv"], "writes": [{"path": "new.csv", "mode": "rename"}]})
        graph = export_graph()
        self.assertEqual(graph["aliases"], {"old.csv": "new.csv"})
        self.assertEqual([node["path"] for node in graph["nodes"]], ["new.csv"])
        self.assertEqual(graph["edges"], [])

    def test_job_lineage_aggregates_to_files(self):
        record({"id": "a1", "batch": "b1", "reads": ["in1.csv", "in2.csv"], "writes": ["mid.csv"]})
        record({"id": "a2", "batch": "b2", "reads": ["mid.csv"], "writes": ["out.csv"]})
        job = job_lineage("a1")
        self.assertTrue(job["exists"])
        self.assertEqual([edge["target"] for edge in job["edges"]], ["mid.csv", "mid.csv"])
        self.assertEqual(sorted(edge["source"] for edge in job["edges"]), ["in1.csv", "in2.csv"])
        self.assertEqual(job["batch"], "b1")
        self.assertEqual(len(job["reads"]), 2)

    def test_export_graph_writes_json(self):
        record({"id": "e1", "batch": "b1", "reads": ["x.csv"], "writes": ["y.csv"]})
        handle, name = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        self.addCleanup(os.remove, name)
        graph = export_graph(name)
        with open(name, encoding="utf-8") as handle:
            loaded = json.load(handle)
        self.assertEqual(loaded, json.loads(json.dumps(graph)))
        self.assertEqual(loaded["batches"], ["b1"])
        self.assertEqual(loaded["jobs"], ["e1"])

    def test_path_normalization_merges_equivalent_paths(self):
        record({"id": "n1", "reads": ["./data/../data/in.csv"], "writes": ["data\\out.csv"]})
        record({"id": "n2", "reads": ["data/out.csv"], "writes": ["final.csv"]})
        result = impact("data/in.csv")
        self.assertEqual(
            sorted(node["path"] for node in result["nodes"]),
            ["data/out.csv", "final.csv"],
        )
        self.assertEqual(result["paths"][-1], ["data/in.csv", "data/out.csv", "final.csv"])

    def test_batch_filter(self):
        record({"id": "bf1", "batch": "b1", "reads": ["a.csv"], "writes": ["b.csv"]})
        record({"id": "bf2", "batch": "b2", "reads": ["b.csv"], "writes": ["c.csv"]})
        self.assertEqual(
            [node["path"] for node in impact("a.csv", batch="b2")["nodes"]],
            [],
        )
        self.assertEqual(impact("a.csv", batch="b1")["depth"], 1)
        self.assertEqual(
            [node["path"] for node in impact("a.csv", batch=["b1", "b2"])["nodes"]],
            ["b.csv", "c.csv"],
        )

    def test_query_by_old_name_resolves_to_renamed_node(self):
        record({"id": "q1", "reads": ["a.csv"], "writes": ["b.csv"],
                "renames": [("a.csv", "a_v2.csv")]})
        result = impact("a.csv")
        self.assertEqual(result["origin"]["path"], "a_v2.csv")
        self.assertTrue(result["origin"]["renamed"])
        self.assertEqual([node["path"] for node in result["nodes"]], ["b.csv"])

    def test_explicit_edges_without_reads_and_writes(self):
        result = record({"id": "x1", "edges": {"a.csv": {"target": "b.csv", "mode": "rb"}}})
        self.assertEqual(result["edges"], 1)
        edge = export_graph()["edges"][0]
        self.assertEqual((edge["source"], edge["target"]), ("a.csv", "b.csv"))
        self.assertEqual(edge["read_modes"], ["rb"])

    def test_unknown_path_returns_no_nodes(self):
        result = impact("missing.csv")
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["depth"], 0)
        traced = lineage_path("missing.csv", "other.csv")
        self.assertFalse(traced["connected"])
        self.assertEqual([item["path"] for item in traced["breakpoints"]], ["missing.csv"])


if __name__ == "__main__":
    unittest.main()
