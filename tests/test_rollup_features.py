import unittest

from metricrollup import add, query, recompute_all, reset, TDigest
from metricrollup.rollup import _buckets, _events


class FeatureTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_batch_split_across_windows_by_event_time(self):
        # 同一批次事件时间跨小时，必须按事件时间拆到两个窗口
        for ts, value in [(0, 1), (3599, 2), (3600, 3), (7199, 4), (7200, 5)]:
            add({"dim": "shop", "ts": ts, "value": value})
        result = query("shop")
        self.assertEqual(result["buckets"], 3)
        starts = [window["window_start"] for window in result["windows"]]
        self.assertEqual(starts, [0, 3600, 7200])
        # 整数时间戳下窗口边界为闭区间 [start, start+3599]
        self.assertEqual(result["windows"][0]["window_end"], 3599)
        self.assertEqual(result["windows"][1]["window_start"], 3600)
        self.assertEqual([w["count"] for w in result["windows"]], [2, 2, 1])
        self.assertEqual(result["count"], 5)
        self.assertEqual(result["sum"], 15)

    def test_window_boundary_is_closed_on_event_time(self):
        add({"dim": "shop", "ts": 3599, "value": 1})
        add({"dim": "shop", "ts": 3600, "value": 2})
        result = query("shop")
        self.assertEqual(result["buckets"], 2)
        self.assertEqual(result["windows"][0]["window_end"], 3599)
        self.assertEqual(result["windows"][1]["window_start"], 3600)

    def test_late_data_triggers_idempotent_recompute(self):
        add({"dim": "shop", "ts": 7200, "value": 10})  # 先推进水位线
        add({"dim": "shop", "ts": 3600, "value": 2})   # 迟到，新窗口
        add({"dim": "shop", "ts": 3601, "value": 4})   # 迟到，同窗口再重算
        result = query("shop")
        self.assertEqual(result["late_recomputations"], 2)
        first = next(w for w in result["windows"] if w["window_start"] == 3600)
        self.assertEqual(first["count"], 2)
        self.assertEqual(first["sum"], 6)
        self.assertEqual(first["recomputations"], 2)

        before = query("shop")
        self.assertEqual(recompute_all(), 2)
        after = query("shop")
        # 重算幂等：计数、求和、草图状态完全一致
        self.assertEqual(after["count"], before["count"])
        self.assertEqual(after["sum"], before["sum"])
        self.assertEqual(after["sketch"], before["sketch"])

    def test_arrival_order_does_not_change_result(self):
        ordered = [(0, 1), (3599, 2), (3600, 3), (10, 4), (7200, 5)]
        for ts, value in ordered:
            add({"dim": "a", "ts": ts, "value": value})
        first = query("a")
        reset()
        for ts, value in reversed(ordered):
            add({"dim": "a", "ts": ts, "value": value})
        second = query("a")
        self.assertEqual(second["buckets"], first["buckets"])
        self.assertEqual(second["count"], first["count"])
        self.assertEqual(second["sum"], first["sum"])
        self.assertEqual(second["sketch"], first["sketch"])

    def test_quantile_sketch_merge_not_average(self):
        # 两个窗口分布差异很大，直接平均 p95 会失真，草图合并应反映全体
        for value in range(1, 21):           # 窗口 0：1..20
            add({"dim": "shop", "ts": 0, "value": value})
        for value in range(100, 120):        # 窗口 3600：100..119
            add({"dim": "shop", "ts": 3600, "value": value})
        result = query("shop")
        window_p95 = [window["p95"] for window in result["windows"]]
        naive_average = sum(window_p95) / len(window_p95)
        self.assertNotAlmostEqual(result["sketch"]["size"], 0)
        # 合并草图基于全部 40 个点，p95 应接近经验 p95（第 38 个点 = 117），而非平均
        merged = TDigest.merge([_buckets[key]["sketch"] for key in _buckets])
        self.assertGreater(merged.quantile(0.95), naive_average + 5)
        self.assertAlmostEqual(merged.quantile(0.95), 117.0, delta=2.0)
        self.assertEqual(result["sketch"]["algorithm"], "t-digest")
        self.assertEqual(result["sketch"]["total_weight"], 40)

    def test_sketch_merge_equivalent_to_single_sketch(self):
        values_a = [3, 1, 4, 1, 5, 9, 2, 6]
        values_b = [8, 0, 7, 2]
        for value in values_a:
            add({"dim": "shop", "ts": 0, "value": value})
        for value in values_b:
            add({"dim": "shop", "ts": 3600, "value": value})
        expected = TDigest.from_values(values_a + values_b)
        merged = TDigest.merge([_buckets[key]["sketch"] for key in _buckets])
        self.assertTrue(merged.equals(expected))
        for fraction in (0.0, 0.5, 0.95, 1.0):
            self.assertEqual(merged.quantile(fraction), expected.quantile(fraction))

    def test_drill_down_rolls_up_consistently(self):
        rows = [
            (("cn", "s1"), 0, 1),
            (("cn", "s1"), 3600, 3),
            (("cn", "s2"), 0, 5),
            (("cn", "s2"), 3600, 7),
            (("us", "s3"), 0, 11),
        ]
        for dim, ts, value in rows:
            add({"dim": dim, "ts": ts, "value": value})

        s1 = query(("cn", "s1"))
        s2 = query(("cn", "s2"))
        cn = query(("cn",))

        # 父维度计数/求和 = 子维度可加合并
        self.assertEqual(cn["count"], s1["count"] + s2["count"])
        self.assertEqual(cn["sum"], s1["sum"] + s2["sum"])
        # 父维度草图 = 子维度草图合并，而非分位数平均
        merged = TDigest.merge([
            _buckets[key]["sketch"] for key in _buckets if key[0][0] == "cn"
        ])
        parent = TDigest.merge([
            _buckets[key]["sketch"] for key in _buckets if key[0][0] == "cn"
        ])
        self.assertTrue(parent.equals(merged))
        self.assertEqual(cn["sketch"], parent.state())
        # 父级只包含 cn，不含 us
        self.assertEqual(cn["count"], 4)
        # 同窗口上卷与下钻一致
        cn_hour0 = query(("cn",), 0)
        self.assertEqual(cn_hour0["sum"], 1 + 5)
        self.assertEqual(cn_hour0["count"], 2)

    def test_window_range_filters(self):
        for ts in (0, 3600, 7200):
            add({"dim": "shop", "ts": ts, "value": 1})
        self.assertEqual(query("shop", 3600)["buckets"], 1)
        self.assertEqual(query("shop", (0, 3600))["buckets"], 2)
        self.assertEqual(query("shop", (3600, 7200))["buckets"], 2)

    def test_output_carries_window_late_and_sketch_state(self):
        add({"dim": "shop", "ts": 3600, "value": 2})
        add({"dim": "shop", "ts": 10, "value": 1})
        result = query("shop")
        self.assertIn("windows", result)
        self.assertIn("late_recomputations", result)
        self.assertIn("sketch", result)
        self.assertEqual(result["late_recomputations"], 1)
        for window in result["windows"]:
            self.assertIn("sketch", window)
            self.assertIn("centroids", window["sketch"])
            self.assertIn("recomputations", window)
        # 原始事件保留，保证可重算
        self.assertEqual(sum(len(events) for events in _events.values()), 2)

    def test_invalid_metric_raises_clear_errors(self):
        with self.assertRaisesRegex(TypeError, "metric 必须是字典"):
            add("not-a-dict")
        with self.assertRaisesRegex(KeyError, "缺少必填字段"):
            add({"dim": "shop", "ts": 1})
        with self.assertRaisesRegex(TypeError, "dim 必须是非空字符串"):
            add({"dim": "", "ts": 1, "value": 1})
        with self.assertRaisesRegex(TypeError, "dim 必须是非空字符串"):
            add({"dim": ("ok", ""), "ts": 1, "value": 1})
        with self.assertRaisesRegex(TypeError, "ts 必须是有限"):
            add({"dim": "shop", "ts": "x", "value": 1})
        with self.assertRaisesRegex(TypeError, "ts 必须是有限"):
            add({"dim": "shop", "ts": float("nan"), "value": 1})
        with self.assertRaisesRegex(TypeError, "value 必须是有限"):
            add({"dim": "shop", "ts": 1, "value": None})
        # 校验失败不得写入任何状态
        self.assertEqual(_events, {})

    def test_invalid_query_raises_clear_errors(self):
        add({"dim": "shop", "ts": 0, "value": 1})
        with self.assertRaisesRegex(KeyError, "没有数据"):
            query("missing")
        with self.assertRaisesRegex(ValueError, "对齐自然小时"):
            query("shop", 1234)
        with self.assertRaisesRegex(TypeError, "window 必须是"):
            query("shop", {"start": 0})
        with self.assertRaisesRegex(ValueError, "end 不能小于 start"):
            query("shop", (7200, 0))
        with self.assertRaisesRegex(TypeError, "dim 必须是非空字符串"):
            query(123)


if __name__ == "__main__":
    unittest.main()
