import importlib
import unittest

from tablelineage2 import record, remove

import tablelineage2.lineage as lineage_mod


class LineageTest(unittest.TestCase):
    def test_record_counts_edges(self):
        job = {"id": "j1", "edges": [{"src": "a", "dst": "b"}, {"src": "b", "dst": "c"}]}
        self.assertEqual(record(job)["edges"], 2)

    def test_record_rebuilds_every_time(self):
        record({"id": "j1", "edges": [{"src": "a", "dst": "b"}]})
        job = {"id": "j2", "edges": [{"src": "c", "dst": "d"}]}
        self.assertEqual(record(job)["edges"], 1)

    def test_remove_keeps_edges(self):
        record({"id": "j1", "edges": [{"src": "a", "dst": "b"}]})
        self.assertEqual(remove({"id": "j1"})["removed"], 0)


class IncrementalLineageTest(unittest.TestCase):
    def setUp(self):
        importlib.reload(lineage_mod)  # 每个用例从空血缘开始

    def record(self, job):
        return lineage_mod.record(job)

    def remove(self, job):
        return lineage_mod.remove(job)

    def query(self, **kw):
        return lineage_mod.query(**kw)

    def edge_set(self):
        return {(r["src"], r["dst"]) for r in self.query()}

    def test_incremental_add_and_idempotent(self):
        r1 = self.record({"id": "j1", "time": 1,
                          "edges": [{"src": "a", "dst": "b"}]})
        self.assertEqual(r1["added"], 1)
        r2 = self.record({"id": "j2", "time": 2,
                          "edges": [{"src": "b", "dst": "c"}]})
        self.assertEqual(r2["added"], 1)
        self.assertEqual(self.edge_set(), {("a", "b"), ("b", "c")})
        # 幂等：同任务同内容重复记录，不新增、不改变边集
        again = self.record({"id": "j2", "time": 2,
                             "edges": [{"src": "b", "dst": "c"}]})
        self.assertEqual(again["added"], 0)
        self.assertEqual(again["removed"], 0)
        self.assertEqual(self.edge_set(), {("a", "b"), ("b", "c")})

    def test_existing_edge_not_duplicated(self):
        self.record({"id": "j1", "time": 1,
                     "edges": [{"src": "a", "dst": "b"}]})
        r = self.record({"id": "j2", "time": 2,
                         "edges": [{"src": "a", "dst": "b"}]})
        self.assertEqual(r["added"], 0)
        self.assertEqual(len(self.query()), 1)

    def test_rerecord_keeps_only_latest_contribution(self):
        self.record({"id": "j1", "time": 1,
                     "edges": [{"src": "a", "dst": "b"},
                               {"src": "b", "dst": "c"}]})
        r = self.record({"id": "j1", "time": 2,
                         "edges": [{"src": "a", "dst": "b"}]})
        self.assertEqual(r["removed"], 1)
        self.assertEqual(self.edge_set(), {("a", "b")})

    def test_remove_cleans_only_own_edges_and_orphans(self):
        j1 = {"id": "j1", "time": 1, "edges": [{"src": "a", "dst": "b"}]}
        j2 = {"id": "j2", "time": 2, "edges": [{"src": "b", "dst": "c"}]}
        self.record(j1)
        self.record(j2)
        r = self.remove(j1)
        self.assertEqual(r["removed"], 1)
        self.assertEqual(self.edge_set(), {("b", "c")})
        # 孤立节点 a 已清理
        self.assertEqual(self.query(table="a"), [])
        self.assertEqual(len(self.query(table="b")), 1)

    def test_remove_ignores_mismatched_fingerprint(self):
        self.record({"id": "j1", "time": 1,
                     "edges": [{"src": "a", "dst": "b"}]})
        # 指纹不匹配（内容不同）时不误删别人的边
        r = self.remove({"id": "j1", "edges": [{"src": "x", "dst": "y"}]})
        self.assertEqual(r["removed"], 0)
        self.assertEqual(self.edge_set(), {("a", "b")})

    def test_cycle_truncated_with_path(self):
        self.record({"id": "j1", "time": 1,
                     "edges": [{"src": "a", "dst": "b"},
                               {"src": "b", "dst": "c"}]})
        r = self.record({"id": "j2", "time": 2,
                         "edges": [{"src": "c", "dst": "a"}]})
        self.assertEqual(r["cycle"], [["c", "a", "b", "c"]])
        self.assertEqual(r["edges"], 0)
        self.assertEqual(self.edge_set(), {("a", "b"), ("b", "c")})

    def test_self_loop_truncated(self):
        r = self.record({"id": "j1", "time": 1,
                         "edges": [{"src": "a", "dst": "a"}]})
        self.assertEqual(r["cycle"], [["a", "a"]])
        self.assertEqual(self.query(), [])

    def test_rename_produces_no_duplicate_edges(self):
        self.record({"id": "old-name", "time": 1,
                     "edges": [{"src": "a", "dst": "b"}]})
        r = self.record({"id": "new-name", "time": 2,
                         "edges": [{"src": "a", "dst": "b"}]})
        self.assertEqual(r["added"], 0)
        rows = self.query()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job"], "new-name")

    def test_query_filters_by_table_and_time(self):
        self.record({"id": "j1", "time": 1,
                     "edges": [{"src": "a", "dst": "b"}]})
        self.record({"id": "j2", "time": 5,
                     "edges": [{"src": "b", "dst": "c"}]})
        self.assertEqual(len(self.query(table="a")), 1)
        self.assertEqual(len(self.query(table="b")), 2)
        self.assertEqual(self.query(table="zzz"), [])
        self.assertEqual(len(self.query(since=2)), 1)
        self.assertEqual(len(self.query(until=2)), 1)
        self.assertEqual(len(self.query(since=1, until=5)), 2)
        self.assertEqual(self.query(since=2, until=4), [])

    def test_incremental_matches_full_replay(self):
        j1 = {"id": "j1", "time": 1, "edges": [{"src": "a", "dst": "b"}]}
        j2 = {"id": "j2", "time": 2, "edges": [{"src": "b", "dst": "c"}]}
        j3 = {"id": "j3", "time": 3, "edges": [{"src": "c", "dst": "d"}]}
        j4 = {"id": "j4", "time": 4, "edges": [{"src": "d", "dst": "e"}]}
        for j in (j1, j2, j3):
            self.record(j)
        self.remove(j2)
        self.record(j4)
        incremental = self.query()

        importlib.reload(lineage_mod)  # 全量重放存活任务
        for j in (j1, j3, j4):
            lineage_mod.record(j)
        self.assertEqual(incremental, lineage_mod.query())
