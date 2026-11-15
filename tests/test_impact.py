"""影响分析测试：字段级、多跳层级、排序、环、断链、改名、缓存、导出回放。"""

import json
import os
import tempfile
import unittest

import lineageimpact2 as li
from lineageimpact2 import Lineage


def _core_graph():
    """一条经典数仓链路：ods -> dwd -> dws -> ads，字段一路传递。

    另有一条 ``dwd.order_log`` 分支，用来验证“同一层级、不同下游”。
    """
    g = Lineage()
    g.add_edge("ods.orders.id", "dwd.order_fact.order_id")
    g.add_edge("dwd.order_fact.order_id", "dws.trade_agg.order_id")
    g.add_edge("dws.trade_agg.order_id", "ads.user_profile.order_id")
    g.add_edge("dwd.order_fact.order_id", "dwd.order_log.order_id")
    return g


class FieldLevelTest(unittest.TestCase):
    def setUp(self):
        self.g = _core_graph()

    def test_field_impact_is_multi_hop(self):
        r = self.g.impact("ods.orders.id")
        self.assertEqual(r["depth"], 3)
        self.assertEqual(
            {e["qualified"] for e in r["impacts"]},
            {
                "dwd.order_fact.order_id",
                "dwd.order_log.order_id",
                "dws.trade_agg.order_id",
                "ads.user_profile.order_id",
            },
        )

    def test_field_is_not_ignored(self):
        r = self.g.impact("ods.orders.id")
        names = {e["qualified"] for e in r["impacts"]}
        self.assertNotIn("dws.trade_agg.user_id", names)
        self.assertNotIn("dwd.order_fact.user_id", names)
        # 表级分析则会覆盖整张表
        self.assertIn("dws.trade_agg", self.g.impact("ods.orders")["downstream"])

    def test_tuple_and_dotted_query_agree(self):
        a = self.g.impact("ods.orders", "id")
        b = self.g.impact("ods.orders.id")
        self.assertEqual(a["impacts"], b["impacts"])

    def test_table_name_may_contain_dots(self):
        g = Lineage()
        g.add_edge("ods.orders.id", "dwd.fact.order_id")
        g.add_edge("dwd.fact.order_id", "dws.agg.uid")
        r = g.impact("ods.orders.id")
        self.assertEqual(r["downstream"], ["dwd.fact", "dws.agg"])

    def test_levels_label_direct_vs_indirect(self):
        r = self.g.impact("ods.orders.id")
        self.assertEqual(r["direct_count"], 1)
        self.assertEqual(r["indirect_count"], 3)
        for e in r["direct_impacts"]:
            self.assertEqual(e["level"], 1)
            self.assertTrue(e["direct"])
            self.assertFalse(e["indirect"])
            self.assertEqual(e["status"], li.DIRECT)
        for e in r["indirect_impacts"]:
            self.assertGreater(e["level"], 1)
            self.assertEqual(e["status"], li.INDIRECT)

    def test_every_impact_carries_table_field_level_path(self):
        r = self.g.impact("ods.orders.id")
        for e in r["impacts"]:
            self.assertTrue(e["table"])
            self.assertTrue(e["field"])
            self.assertGreaterEqual(e["level"], 1)
            self.assertEqual(len(e["path"]), e["level"] + 1)
            self.assertEqual(e["path"][0], "ods.orders.id")
            self.assertEqual(e["path"][-1], e["qualified"])

    def test_sorted_by_impact_descending(self):
        r = self.g.impact("ods.orders.id")
        scores = [e["impact"] for e in r["impacts"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(scores[0], 1.0)
        self.assertEqual([e["level"] for e in r["impacts"]][:3], [1, 2, 2])
        self.assertEqual(r["path_scores"], sorted(r["path_scores"], reverse=True))

    def test_one_node_keeps_best_path(self):
        """菱形依赖：同一节点只出现一次，保留影响最大的路径。"""
        g = Lineage()
        g.add_edge("a.x", "b.y", 0.9)
        g.add_edge("a.x", "c.z", 0.2)
        g.add_edge("c.z", "b.y", 1.0)
        r = g.impact("a.x")
        hit = [e for e in r["impacts"] if e["qualified"] == "b.y"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["level"], 1)
        self.assertAlmostEqual(hit[0]["impact"], 0.9)

    def test_weight_changes_ranking(self):
        g = Lineage()
        g.add_edge("a.x", "b.y", 0.1)
        g.add_edge("a.x", "c.z", 0.9)
        self.assertEqual([e["qualified"] for e in g.impact("a.x")["impacts"]],
                         ["c.z", "b.y"])
        g.set_edge_weight("a.x", "b.y", 0.95)
        self.assertEqual([e["qualified"] for e in g.impact("a.x")["impacts"]],
                         ["b.y", "c.z"])
        self.assertEqual(g.impact("a.x")["cache"]["version"], g.version)

    def test_table_level_analysis(self):
        r = self.g.impact("ods.orders")
        self.assertEqual(r["downstream"], ["ads.user_profile", "dwd.order_fact",
                                           "dwd.order_log", "dws.trade_agg"])
        self.assertEqual(r["query"]["field"], None)
        self.assertEqual(r["depth"], 3)
    def test_unknown_start_node_reports_break(self):
        r = self.g.impact("no.such.table")
        self.assertTrue(r["missing"])
        self.assertEqual(r["impacts"], [])
        self.assertEqual(r["breaks"][0]["reason"], "起始节点不在血缘图中")


class CycleTest(unittest.TestCase):
    def test_cycle_is_truncated_and_reported(self):
        g = Lineage()
        g.add_edge("a.x", "b.y")
        g.add_edge("b.y", "c.z")
        g.add_edge("c.z", "a.x")
        r = g.impact("a.x")
        self.assertTrue(r["truncated"])
        self.assertIn("cycle", r["truncation"])
        self.assertTrue(r["cycles"])
        # 起点不会被当成下游再算一次
        self.assertNotIn("a.x", {e["qualified"] for e in r["impacts"]})
        self.assertEqual(r["total"], 2)

    def test_self_loop_terminates(self):
        g = Lineage()
        g.add_edge("a.x", "b.y")
        g.add_edge("b.y", "b.y")
        r = g.impact("a.x")
        self.assertEqual(r["total"], 1)
        self.assertTrue(r["cycles"])

    def test_two_cycle_does_not_hang(self):
        g = Lineage()
        g.add_edge("a.x", "b.y")
        g.add_edge("b.y", "a.x")
        r = g.impact("a.x")
        self.assertEqual(r["total"], 1)
        self.assertEqual(len(r["cycles"]), 1)

    def test_max_depth_truncation(self):
        g = Lineage()
        for i in range(10):
            g.add_edge(f"t{i}.f", f"t{i + 1}.f")
        r = g.impact("t0.f", max_depth=3)
        self.assertEqual(r["depth"], 3)
        self.assertEqual(r["total"], 3)
        self.assertIn("depth", r["truncation"])
        self.assertTrue(r["truncated"])
        self.assertEqual(g.impact("t0.f", max_depth=20)["depth"], 10)


class BreakTest(unittest.TestCase):
    def test_table_edge_marks_breakpoint(self):
        g = Lineage()
        g.add_edge("ods.orders.id", "dwd.order_fact.order_id")
        g.add_table_edge("dwd.order_fact", "dws.trade_agg")
        g.add_edge("dws.trade_agg.uid", "ads.user_profile.uid")
        r = g.impact("ods.orders.id")
        partial = [e for e in r["impacts"] if e["partial"]]
        self.assertTrue(partial)
        bp = partial[0]["breakpoint"]
        self.assertEqual(bp["from"], "dwd.order_fact.order_id")
        self.assertEqual(bp["to"], "dws.trade_agg")
        self.assertIn("字段映射未知", bp["reason"])
        self.assertEqual(bp["path"][0], "ods.orders.id")
        self.assertTrue(any(b["to"] == "dws.trade_agg" for b in r["breaks"]))
        # 断在表上，就不会再顺着表反查出具体字段
        self.assertNotIn("ads.user_profile.uid",
                         {e["qualified"] for e in r["impacts"]})

    def test_break_does_not_continue_into_unknown_fields(self):
        """截断在表级，不再凭想象往下钻到字段。"""
        g = Lineage()
        g.add_edge("a.f", "b.f")
        g.add_table_edge("b", "c")
        g.add_edge("c.g", "d.h")
        r = g.impact("a.f")
        self.assertNotIn("d.h", {e["qualified"] for e in r["impacts"]})

    def test_breakpoint_is_terminal(self):
        """断点表自身的已知字段边也不该被当作受影响字段。"""
        g = Lineage()
        g.add_edge("a.f", "b.f")
        g.add_table_edge("b", "c")
        g.add_edge("c.g", "d.h")
        r = g.impact("a.f")
        names = {e["qualified"] for e in r["impacts"]}
        self.assertIn("c", names)
        self.assertNotIn("d.h", names)
        self.assertEqual([b["to"] for b in r["breaks"]], ["c"])


class RenameTest(unittest.TestCase):
    def test_table_rename_keeps_single_node(self):
        g = _core_graph()
        before = g.field_count()
        g.rename_table("dwd.order_fact", "dwd.trade_fact")
        self.assertEqual(g.field_count(), before)
        r = g.impact("ods.orders.id")
        names = {e["qualified"] for e in r["impacts"]}
        self.assertIn("dwd.trade_fact.order_id", names)
        self.assertNotIn("dwd.order_fact.order_id", names)
        # 旧名仍解析到同一节点，不产生重复
        self.assertEqual(g.impact("ods.orders.id")["total"], r["total"])

    def test_field_rename_keeps_single_node(self):
        g = _core_graph()
        before = g.field_count()
        g.rename_field(("dwd.order_fact", "order_id"), "order_key")
        g.rename_field("dwd.order_fact.order_key", "order_key2")
        self.assertEqual(g.field_count(), before)
        self.assertIn("dwd.order_fact.order_key2",
                      {e["qualified"] for e in g.impact("ods.orders.id")["impacts"]})
        # 最初的旧名依然指向同一个字段
        self.assertEqual(g.field_id("dwd.order_fact.order_id"),
                         g.field_id("dwd.order_fact.order_key2"))

    def test_repeated_add_after_rename_no_duplicate(self):
        g = Lineage()
        g.add_edge("a.x", "b.y")
        g.rename_table("b", "b2")
        g.add_edge("a.x", "b2.y")  # 用新名再加一次
        self.assertEqual(g.table_count(), 2)
        self.assertEqual(g.field_count(), 2)
        self.assertEqual(g.impact("a.x")["total"], 1)

    def test_rename_to_existing_name_rejected(self):
        g = _core_graph()
        with self.assertRaises(ValueError):
            g.rename_table("dwd.order_fact", "dws.trade_agg")


class CacheTest(unittest.TestCase):
    def test_second_query_hits_cache(self):
        g = _core_graph()
        first = g.impact("ods.orders.id")
        second = g.impact("ods.orders.id")
        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])
        self.assertEqual(first["impacts"], second["impacts"])

    def test_cache_invalidated_on_lineage_change(self):
        g = _core_graph()
        self.assertEqual(g.impact("ods.orders.id")["total"], 4)
        g.add_edge("ads.user_profile.order_id", "app.portal.order_id")
        after = g.impact("ods.orders.id")
        self.assertFalse(after["cache"]["hit"])
        self.assertEqual(after["total"], 5)
        self.assertIn("app.portal.order_id",
                      {e["qualified"] for e in after["impacts"]})

    def test_cache_invalidated_on_rename_and_removal(self):
        g = _core_graph()
        g.impact("ods.orders.id")
        g.rename_table("ads.user_profile", "ads.profile")
        hit = g.impact("ods.orders.id")
        self.assertFalse(hit["cache"]["hit"])
        self.assertIn("ads.profile.order_id", {e["qualified"] for e in hit["impacts"]})

        g.impact("ods.orders.id")
        g.remove_edge("dws.trade_agg.order_id", "ads.profile.order_id")
        after = g.impact("ods.orders.id")
        self.assertFalse(after["cache"]["hit"])
        self.assertNotIn("ads.profile.order_id",
                         {e["qualified"] for e in after["impacts"]})

    def test_cached_result_is_isolated(self):
        g = _core_graph()
        r1 = g.impact("ods.orders.id")
        r1["impacts"].clear()
        r2 = g.impact("ods.orders.id")
        self.assertEqual(len(r2["impacts"]), 4)


class VerifyAndExportTest(unittest.TestCase):
    def test_verify_matches_expected(self):
        g = _core_graph()
        good = g.verify("ods.orders.id", None, [
            "dwd.order_fact.order_id", "dwd.order_log.order_id",
            "dws.trade_agg.order_id", "ads.user_profile.order_id",
        ])
        self.assertTrue(good["ok"])
        bad = g.verify("ods.orders.id", None, ["dwd.order_fact.order_id"])
        self.assertFalse(bad["ok"])
        self.assertIn("dws.trade_agg.order_id", bad["unexpected"])

    def test_export_json_and_csv(self):
        g = _core_graph()
        r = g.impact("ods.orders.id")
        payload = json.loads(li.export(r, "json"))
        self.assertEqual(payload["total"], 4)
        csv_text = li.export(r, "csv")
        self.assertTrue(csv_text.startswith("table,field,level,status,impact,partial,path"))
        self.assertIn("dwd.order_fact,order_id,1,direct", csv_text)

    def test_save_infers_format(self):
        g = _core_graph()
        r = g.impact("ods.orders.id")
        with tempfile.TemporaryDirectory() as d:
            json_path = os.path.join(d, "out.json")
            csv_path = os.path.join(d, "out.csv")
            li.save(r, json_path)
            li.save(r, csv_path)
            with open(json_path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["total"], 4)
            with open(csv_path, encoding="utf-8") as fh:
                self.assertIn("direct", fh.read())


class ReplayTest(unittest.TestCase):
    def test_replay_rebuilds_same_graph(self):
        g = _core_graph()
        g.rename_table("dwd.order_fact", "dwd.trade_fact")
        g.set_edge_weight("ods.orders.id", "dwd.trade_fact.order_id", 0.7)
        rebuilt = Lineage.from_events(g.events())
        self.assertEqual(rebuilt.to_dict()["field_edges"], g.to_dict()["field_edges"])
        self.assertEqual(rebuilt.impact("ods.orders.id")["impacts"],
                         g.impact("ods.orders.id")["impacts"])

    def test_replay_into_existing_graph(self):
        g = Lineage()
        g.add_edge("a.x", "b.y")
        events = Lineage.from_events([]).events()
        g.replay(events)
        self.assertEqual(g.impact("a.x")["total"], 1)


class DefaultGraphTest(unittest.TestCase):
    def setUp(self):
        li.reset()

    def test_module_level_helpers(self):
        li.add_edge("ods.orders.id", "dwd.order_fact.order_id")
        li.add_edge("dwd.order_fact.order_id", "dws.trade_agg.order_id")
        self.assertEqual(li.impact("ods.orders.id")["depth"], 2)
        self.assertEqual(li.paths("ods.orders.id")["paths"][-1],
                         ["ods.orders.id", "dwd.order_fact.order_id",
                          "dws.trade_agg.order_id"])

    def test_paths_reports_cycles(self):
        li.add_edge("a.x", "b.y")
        li.add_edge("b.y", "a.x")
        info = li.paths("a.x")
        self.assertTrue(info["cycles"])
        self.assertTrue(info["truncated"])


if __name__ == "__main__":
    unittest.main()
