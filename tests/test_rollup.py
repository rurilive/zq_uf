import json
import unittest

from metricrollup2 import Sketch, add, configure, query, recompute, reset, stats


class RollupTest(unittest.TestCase):
    def test_add_accumulates(self):
        add({"dim": "a", "window": "w1", "value": 2})
        self.assertEqual(add({"dim": "a", "window": "w1", "value": 3})["count"], 2)

    def test_query_sum(self):
        add({"dim": "b", "window": "w2", "value": 4})
        self.assertEqual(query("b", "w2")["sum"], 4.0)

    def test_p50_is_sample_mean(self):
        add({"dim": "c", "window": "w3", "value": 1})
        add({"dim": "c", "window": "w3", "value": 3})
        self.assertEqual(query("c", "w3")["p50"], 2.0)


class EventTimeWindowTest(unittest.TestCase):
    """跨窗批次按事件时间切分，查询带窗口边界。"""

    def setUp(self):
        reset()
        configure(window_size=60, retention_windows=100)

    def tearDown(self):
        reset()

    def test_batch_split_by_event_time(self):
        r = add({"dim": "s", "events": [
            {"ts": 1, "value": 1},
            {"ts": 61, "value": 2},
            {"ts": 122, "value": 3},
        ]})
        self.assertEqual(r["added"], 3)
        self.assertEqual(query("s", 0)["count"], 1)
        self.assertEqual(query("s", 60)["count"], 1)
        self.assertEqual(query("s", 120)["count"], 1)
        self.assertEqual(query("s", 120)["sum"], 3.0)

    def test_query_returns_window_bounds(self):
        add({"dim": "s", "ts": 65, "value": 1})
        q = query("s", 65)
        self.assertEqual(q["window_start"], 60)
        self.assertEqual(q["window_end"], 120)
        empty = query("s", 300)
        self.assertEqual((empty["window_start"], empty["window_end"]), (300, 360))


class LateRecomputeTest(unittest.TestCase):
    """迟到数据触发相关窗口重算，重算幂等，同 id 去重。"""

    def setUp(self):
        reset()
        configure(window_size=60, retention_windows=100)

    def tearDown(self):
        reset()

    def test_late_event_triggers_recompute(self):
        add({"dim": "s", "ts": 10, "value": 1})
        add({"dim": "s", "ts": 70, "value": 2})
        r = add({"dim": "s", "ts": 20, "value": 3})
        self.assertTrue(r["late"])
        q = query("s", 0)
        self.assertEqual(q["count"], 2)
        self.assertEqual(q["sum"], 4.0)
        self.assertEqual(q["recomputes"], 1)
        self.assertEqual(q["late_events"], 1)

    def test_recompute_idempotent(self):
        add({"dim": "s", "ts": 10, "value": 1})
        add({"dim": "s", "ts": 70, "value": 2})
        add({"dim": "s", "ts": 20, "value": 3})
        before = query("s", 0)
        recompute("s", 0)
        recompute("s", 0)
        after = query("s", 0)
        self.assertEqual(before["count"], after["count"])
        self.assertEqual(before["sum"], after["sum"])
        self.assertEqual(before["p50"], after["p50"])

    def test_duplicate_event_id_not_counted_twice(self):
        add({"dim": "s", "ts": 10, "value": 1, "id": "e1"})
        r = add({"dim": "s", "ts": 10, "value": 1, "id": "e1"})
        self.assertTrue(r["duplicate"])
        self.assertEqual(query("s", 0)["count"], 1)


class SketchMergeTest(unittest.TestCase):
    """分位数草图可合并、可序列化，上卷与下钻一致，误差在允许范围内。"""

    def setUp(self):
        reset()
        configure(window_size=1000, retention_windows=10, sketch_capacity=16)

    def tearDown(self):
        reset()

    def test_rollup_matches_drilldown(self):
        for i in range(200):
            add({"dim": "d1" if i % 2 == 0 else "d2", "ts": i, "value": float(i)})
        q1, q2, qall = query("d1", 0), query("d2", 0), query("*", 0)
        self.assertEqual(qall["count"], q1["count"] + q2["count"])
        self.assertEqual(qall["sum"], q1["sum"] + q2["sum"])
        self.assertAlmostEqual(qall["p50"], 99.5, delta=10.0)

    def test_rollup_drilldown_exact_when_uncompressed(self):
        reset()
        configure(window_size=60)
        add({"dim": "d1", "ts": 1, "value": 1})
        add({"dim": "d1", "ts": 2, "value": 3})
        add({"dim": "d2", "ts": 3, "value": 5})
        qall = query("*", 0)
        self.assertEqual(qall["count"], 3)
        self.assertEqual(qall["sum"], 9.0)
        self.assertEqual(qall["p50"], 3.0)

    def test_sketch_merge_and_serialize(self):
        s1, s2 = Sketch(capacity=16), Sketch(capacity=16)
        for i in range(100):
            s1.add(i)
        for i in range(100, 200):
            s2.add(i)
        merged = s1.merge(s2)
        restored = Sketch.from_dict(json.loads(json.dumps(merged.to_dict())))
        self.assertEqual(restored.to_dict(), merged.to_dict())
        self.assertAlmostEqual(merged.quantile(0.5), 99.5, delta=10.0)
        self.assertAlmostEqual(restored.quantile(0.5), 99.5, delta=10.0)


class RetentionAndIndexTest(unittest.TestCase):
    """窗口保留期可配置，维度基数变化时重建索引。"""

    def setUp(self):
        reset()
        configure(window_size=10, retention_windows=2)

    def tearDown(self):
        reset()

    def test_retention_expires_old_windows(self):
        for ts in (0, 10, 20, 30):
            add({"dim": "s", "ts": ts, "value": 1})
        self.assertEqual(query("s", 0)["count"], 0)
        self.assertEqual(query("s", 10)["count"], 0)
        self.assertEqual(query("s", 20)["count"], 1)
        self.assertEqual(query("s", 30)["count"], 1)

    def test_index_rebuilt_on_cardinality_change(self):
        r0 = stats()["index_rebuilds"]
        add({"dim": "d1", "ts": 0, "value": 1})
        r1 = stats()["index_rebuilds"]
        self.assertEqual(r1, r0 + 1)
        add({"dim": "d1", "ts": 1, "value": 1})
        self.assertEqual(stats()["index_rebuilds"], r1)
        add({"dim": "d2", "ts": 2, "value": 1})
        self.assertEqual(stats()["index_rebuilds"], r1 + 1)
        self.assertEqual(stats()["dims"], 2)

    def test_index_rebuilt_when_dim_expires(self):
        add({"dim": "gone", "ts": 0, "value": 1})
        r0 = stats()["index_rebuilds"]
        add({"dim": "stay", "ts": 50, "value": 1})
        self.assertEqual(query("gone", 0)["count"], 0)
        self.assertGreater(stats()["index_rebuilds"], r0)
        self.assertEqual(stats()["dims"], 1)
