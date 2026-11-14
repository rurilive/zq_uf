import importlib
import unittest

from lineageimpact import impact, paths

module = importlib.import_module("lineageimpact.impact")


class ImpactTest(unittest.TestCase):
    def setUp(self):
        module._edges.clear()
        module.record("raw", "dwd")

    def test_direct_downstream(self):
        self.assertEqual(impact("raw"), ["dwd"])

    def test_field_ignored(self):
        self.assertEqual(impact("raw", "id"), impact("raw"))

    def test_paths_single(self):
        self.assertEqual(paths("raw"), ["raw"])


class GraphTestBase(unittest.TestCase):
    """新增用例共用：每个用例前后都清空图与缓存，避免顺序耦合。"""

    def setUp(self):
        module.reset()

    def tearDown(self):
        module.reset()


class MultiHopTest(GraphTestBase):
    def setUp(self):
        super().setUp()
        module.record("raw", "dwd")
        module.record("dwd", "dm")
        module.record("dm", "ads")

    def test_three_hops_visible(self):
        self.assertEqual(impact("raw"), ["dwd", "dm", "ads"])

    def test_levels(self):
        result = impact("raw")
        self.assertEqual(
            [(item["table"], item["level"]) for item in result.details],
            [("dwd", 1), ("dm", 2), ("ads", 3)],
        )

    def test_paths_in_details(self):
        result = impact("raw")
        by_table = {item["table"]: item["paths"] for item in result.details}
        self.assertEqual(by_table["ads"], ["raw -> dwd -> dm -> ads"])

    def test_sorted_by_impact(self):
        # 近的先出；把一条更近的边加进来，它必须排在前面。
        module.record("raw", "ods")
        self.assertEqual(impact("raw"), ["dwd", "ods", "dm", "ads"])

    def test_multi_hop_paths(self):
        self.assertEqual(paths("ads"), ["raw -> dwd -> dm -> ads"])

    def test_diamond_not_treated_as_cycle(self):
        module.record("raw", "dwd2")
        module.record("dwd", "dm")
        module.record("dwd2", "dm")
        result = impact("raw")
        self.assertIn("dm", result)
        self.assertEqual(result.cycles, [])

    def test_multi_hop_field_aggregation(self):
        # 同一个表被两条路径命中时，表只出现一次，路径都保留。
        module.record("raw", "b")
        module.record("raw", "c")
        module.record("b", "d")
        module.record("c", "d")
        result = impact("raw")
        self.assertEqual(result.count("d"), 1)
        paths_for_d = [i["paths"] for i in result.details if i["table"] == "d"][0]
        self.assertEqual(paths_for_d, ["raw -> b -> d", "raw -> c -> d"])


class FieldLevelTest(GraphTestBase):
    def setUp(self):
        super().setUp()
        module.record_field("raw", "id", "dwd", "user_id")
        module.record_field("dwd", "user_id", "dm", "uid")
        module.record_field("raw", "name", "dwd", "user_name")

    def test_field_edges_visible(self):
        self.assertEqual(impact("raw", "id"), ["dwd", "dm"])

    def test_field_level_detail(self):
        result = impact("raw", "id")
        self.assertEqual(
            [(f["table"], f["field"], f["level"]) for f in result.fields],
            [("dwd", "user_id", 1), ("dm", "uid", 2)],
        )

    def test_field_paths(self):
        result = impact("raw", "id")
        self.assertEqual(
            result.fields[1]["paths"], ["raw.id -> dwd.user_id -> dm.uid"]
        )

    def test_field_does_not_leak_sibling(self):
        # name 的字段级下游不应出现在 id 的结果里。
        self.assertEqual(impact("raw", "name"), ["dwd"])

    def test_table_query_sees_field_edges(self):
        # 字段级边会补出表级边，表级查询能看到 dwd。
        self.assertIn("dwd", impact("raw"))

    def test_unmapped_field_falls_back_to_table(self):
        result = impact("raw", "no_such_field")
        self.assertTrue(result.field_fallback)
        self.assertIn("dwd", result)

    def test_field_multi_hop_paths(self):
        self.assertEqual(paths(("raw", "id")), ["raw.id"])
        self.assertEqual(
            paths("dwd.user_id"), ["raw.id -> dwd.user_id"]
        )
        self.assertEqual(
            paths(("dm", "uid")), ["raw.id -> dwd.user_id -> dm.uid"]
        )


class CycleTest(GraphTestBase):
    def setUp(self):
        super().setUp()
        module.record("a", "b")
        module.record("b", "c")
        module.record("c", "a")

    def test_cycle_truncated(self):
        result = impact("a")
        # b、c 可达；回到 a 时截断，a 不会作为自己的下游出现。
        self.assertEqual(result, ["b", "c"])
        self.assertEqual(result.cycles, ["a -> b -> c -> a (cycle)"])

    def test_cycle_bounded(self):
        # 环路不展开成无限路径。
        result = impact("a")
        for item in result.details:
            self.assertLessEqual(len(item["paths"]), 2)

    def test_self_loop(self):
        module.record("s", "s")
        result = impact("s")
        # 源表永远不算作自己的下游；自环只作为环路记录并截断。
        self.assertEqual(result, [])
        self.assertIn("s -> s (cycle)", result.cycles)
        self.assertEqual(list(paths("s")), ["s -> s (cycle)"])

    def test_paths_cycle_terminates(self):
        result = paths("c")
        self.assertEqual(list(result), ["c -> a -> b -> c (cycle)"])
        self.assertTrue(result.cycles)
        self.assertTrue(all("(cycle)" in item for item in result))


class CacheTest(GraphTestBase):
    def setUp(self):
        super().setUp()
        module.record("raw", "dwd")
        module.record("dwd", "dm")

    def test_second_query_hits_cache(self):
        first = impact("raw")
        second = impact("raw")
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(first, second)

    def test_cache_invalidated_on_new_edge(self):
        self.assertEqual(impact("raw"), ["dwd", "dm"])
        module.record("dm", "ads")
        result = impact("raw")
        self.assertFalse(result.cache_hit)
        self.assertEqual(result, ["dwd", "dm", "ads"])

    def test_cache_invalidated_on_field_edge(self):
        self.assertEqual(impact("raw", "id"), ["dwd", "dm"])
        module.record_field("dm", "uid", "ads", "final_id")
        result = impact("raw", "id")
        self.assertFalse(result.cache_hit)
        self.assertIn("ads", result)

    def test_manual_invalidate(self):
        impact("raw")
        module.invalidate()
        self.assertFalse(impact("raw").cache_hit)

    def test_cached_result_is_isolated(self):
        result = impact("raw")
        result.append("tampered")
        self.assertEqual(impact("raw"), ["dwd", "dm"])

    def test_hit_rate_reported_separately(self):
        impact("raw")
        impact("raw")
        impact("raw")
        paths("dm")
        stats = module.cache_stats()
        self.assertEqual(stats["by_kind"]["impact"]["hits"], 2)
        self.assertEqual(stats["by_kind"]["impact"]["misses"], 1)
        self.assertEqual(stats["by_kind"]["paths"]["misses"], 1)
        self.assertAlmostEqual(module.cache_hit_rate(), 2 / 4)

    def test_stats_reset(self):
        impact("raw")
        module.reset()
        stats = module.cache_stats()
        self.assertEqual(stats["hits"], 0)
        self.assertEqual(stats["misses"], 0)
        self.assertEqual(stats["size"], 0)

