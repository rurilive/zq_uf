import json
import math
import random
import unittest

from metricroll3 import DDSketch, RollStore, add, add_batch, drilldown, query, reset

BASE = 1_700_000_040  # 60 秒对齐


class WindowLayoutTest(unittest.TestCase):
    def setUp(self):
        self.store = RollStore(window_seconds=60, ttl_seconds=3600, alpha=0.01)

    def test_batch_splits_by_event_time(self):
        result = self.store.add_batch([
            {"dim": "shop", "event_time": BASE + 70, "value": 5},
            {"dim": "shop", "event_time": BASE + 10, "value": 1},
            {"dim": "shop", "event_time": BASE + 20, "values": [2, 3]},
        ])
        by_start = {row["window_start"]: row for row in result}
        self.assertEqual(len(by_start), 2)
        first = by_start["2023-11-14T22:14:00Z"]
        second = by_start["2023-11-14T22:15:00Z"]
        self.assertEqual((first["count"], first["sum"]), (3, 6.0))
        self.assertEqual((second["count"], second["sum"]), (1, 5.0))

    def test_same_second_keeps_input_order(self):
        self.store.add({"dim": "d", "event_time": BASE, "value": 1})
        self.store.add({"dim": "d", "event_time": BASE, "value": 2})
        row = self.store.query("d", start=BASE, end=BASE + 60)
        self.assertEqual((row["count"], row["sum"]), (2, 3.0))

    def test_query_returns_window_bounds(self):
        self.store.add({"dim": "d", "event_time": BASE, "value": 1})
        row = self.store.query("d", start=BASE, end=BASE + 120)
        self.assertEqual(row["window_start"], "2023-11-14T22:14:00Z")
        self.assertEqual(row["window_end"], "2023-11-14T22:16:00Z")
        self.assertEqual(len(row["windows"]), 2)

    def test_single_window_query(self):
        self.store.add({"dim": "d", "event_time": BASE + 5, "value": 1})
        row = self.store.query("d", BASE)
        self.assertEqual(row["window_start"], "2023-11-14T22:14:00Z")
        self.assertEqual(row["window_end"], "2023-11-14T22:15:00Z")

    def test_unaligned_range_rejected(self):
        with self.assertRaises(ValueError):
            self.store.query("d", start=BASE + 1, end=BASE + 60)


class LateDataTest(unittest.TestCase):
    def setUp(self):
        self.store = RollStore(window_seconds=60, ttl_seconds=3600, alpha=0.01)

    def test_late_event_only_recomputes_its_window(self):
        self.store.add({"dim": "d", "event_time": BASE + 10, "value": 1})
        self.store.add({"dim": "d", "event_time": BASE + 70, "value": 10})
        result = self.store.add({"dim": "d", "event_time": BASE + 20, "value": 2})

        self.assertEqual(result["window_start"], "2023-11-14T22:14:00Z")
        self.assertTrue(result["recomputed"])

        first = self.store.query("d", start=BASE, end=BASE + 60)
        second = self.store.query("d", start=BASE + 60, end=BASE + 120)
        self.assertEqual((first["count"], first["sum"]), (2, 3.0))
        self.assertTrue(first["recomputed"])
        self.assertFalse(second["recomputed"])
        self.assertEqual((second["count"], second["sum"]), (1, 10.0))

    def test_late_within_batch_not_marked_late(self):
        # 同一批里乱序到达但都早于水位线 → 不应误判为迟到。
        result = self.store.add_batch([
            {"dim": "d", "event_time": BASE + 50, "value": 1},
            {"dim": "d", "event_time": BASE + 5, "value": 2},
        ])
        self.assertTrue(all(not row["recomputed"] for row in result))
        self.assertEqual(self.store.query("d", start=BASE, end=BASE + 60)["sum"], 3.0)

    def test_late_event_does_not_change_other_windows(self):
        self.store.add({"dim": "d", "event_time": BASE, "value": 1})
        self.store.add({"dim": "d", "event_time": BASE + 120, "value": 1})
        self.store.add({"dim": "d", "event_time": BASE + 10, "value": 5})
        rows = self.store.drilldown("d", start=BASE, end=BASE + 180)
        self.assertEqual([r["sum"] for r in rows], [6.0, 0.0, 1.0])


class ExpiryTest(unittest.TestCase):
    def test_expired_windows_are_purged(self):
        store = RollStore(window_seconds=60, ttl_seconds=120, alpha=0.01)
        store.add({"dim": "d", "event_time": BASE, "value": 1})
        store.add({"dim": "d", "event_time": BASE + 600, "value": 9})

        old = store.query("d", start=BASE, end=BASE + 60)
        self.assertEqual((old["count"], old["sum"]), (0, 0.0))
        self.assertTrue(old["expired"])

        fresh = store.query("d", start=BASE + 600, end=BASE + 660)
        self.assertEqual(fresh["sum"], 9.0)
        self.assertFalse(fresh["expired"])

    def test_late_to_expired_window_is_dropped(self):
        store = RollStore(window_seconds=60, ttl_seconds=120, alpha=0.01)
        store.add({"dim": "d", "event_time": BASE, "value": 1})
        store.add({"dim": "d", "event_time": BASE + 600, "value": 9})
        result = store.add({"dim": "d", "event_time": BASE + 30, "value": 7})
        self.assertTrue(result["dropped"])
        self.assertTrue(result["expired"])
        self.assertEqual(result["count"], 0)

    def test_ttl_none_keeps_everything(self):
        store = RollStore(window_seconds=60, ttl_seconds=None, alpha=0.01)
        store.add({"dim": "d", "event_time": BASE, "value": 1})
        store.add({"dim": "d", "event_time": BASE + 600, "value": 9})
        self.assertEqual(store.query("d", start=BASE, end=BASE + 60)["sum"], 1.0)


class QuantileTest(unittest.TestCase):
    def setUp(self):
        self.store = RollStore(window_seconds=60, ttl_seconds=3600, alpha=0.01)

    def test_hand_computed_window(self):
        # 已知窗口：1..9，手工算 sum=45、count=9、中位数=5。
        for value in range(1, 10):
            self.store.add({"dim": "d", "event_time": BASE, "value": value})
        row = self.store.query("d", start=BASE, end=BASE + 60)
        self.assertEqual(row["count"], 9)
        self.assertEqual(row["sum"], 45.0)
        self.assertLess(abs(row["quantiles"]["p50"] - 5.0) / 5.0, 0.01)
        self.assertLess(abs(row["quantiles"]["p90"] - 9.0) / 9.0, 0.01)

    def test_quantile_error_within_alpha(self):
        rng = random.Random(1234)
        values = [rng.uniform(0.5, 1000.0) for _ in range(4000)]
        for value in values:
            self.store.add({"dim": "d", "event_time": BASE, "value": value})
        row = self.store.query("d", start=BASE, end=BASE + 60,
                               quantiles=["p50", "p90", "p99"])
        ordered = sorted(values)
        for q, label in ((0.5, "p50"), (0.9, "p90"), (0.99, "p99")):
            # 与实现的最近秩定义对齐：第 ceil(q*n) 个样本。
            exact = ordered[max(0, math.ceil(q * len(ordered)) - 1)]
            got = row["quantiles"][label]
            self.assertLessEqual(abs(got - exact) / exact, 0.011)
            bound = row["error"][label]
            self.assertLessEqual(bound["low"], got)
            self.assertGreaterEqual(bound["high"], got)
            self.assertEqual(bound["relative"], 0.01)

    def test_rollup_is_not_average_of_quantiles(self):
        # 两个窗口：一个集中在 1..10，一个集中在 1000..1010。
        for value in range(1, 11):
            self.store.add({"dim": "d", "event_time": BASE, "value": value})
        for value in range(1000, 1011):
            self.store.add({"dim": "d", "event_time": BASE + 60, "value": value})

        rows = self.store.drilldown("d", start=BASE, end=BASE + 120)
        averaged = (rows[0]["quantiles"]["p50"] + rows[1]["quantiles"]["p50"]) / 2
        rollup = self.store.query("d", start=BASE, end=BASE + 120)["quantiles"]["p50"]

        self.assertLess(abs(rollup - 1000.5), 20.0)   # 合并分布后取分位
        self.assertGreater(abs(averaged - rollup), 400.0)  # 直接平均明显失真

    def test_drilldown_merges_to_rollup(self):
        rng = random.Random(7)
        for _ in range(3000):
            self.store.add({"dim": "d",
                            "event_time": BASE + rng.randrange(0, 180),
                            "value": rng.uniform(1, 500)})
        rollup = self.store.query("d", start=BASE, end=BASE + 180)
        rows = self.store.drilldown("d", start=BASE, end=BASE + 180)
        self.assertEqual(rollup["sum"], sum(r["sum"] for r in rows))
        self.assertEqual(rollup["count"], sum(r["count"] for r in rows))

        merged = DDSketch.from_dict(rows[0]["distribution"])
        for row in rows[1:]:
            merged.merge(DDSketch.from_dict(row["distribution"]))
        for q in (0.5, 0.9, 0.99):
            label = f"p{int(q * 100)}"
            self.assertAlmostEqual(merged.quantile(q), rollup["quantiles"][label], places=9)

    def test_merge_order_independent(self):
        parts = []
        rng = random.Random(42)
        for _ in range(5):
            sketch = DDSketch(0.01)
            for _ in range(200):
                sketch.add(rng.uniform(1, 100))
            parts.append(sketch)
        forward = DDSketch(0.01)
        for part in parts:
            forward.merge(part)
        backward = DDSketch(0.01)
        for part in reversed(parts):
            backward.merge(part)
        for q in (0.1, 0.5, 0.9, 0.99):
            self.assertEqual(forward.quantile(q), backward.quantile(q))

    def test_sketch_roundtrip_json(self):
        sketch = DDSketch(0.01)
        for value in (-5, 0, 1, 2.5, 100):
            sketch.add(value)
        clone = DDSketch.from_dict(json.loads(json.dumps(sketch.to_dict())))
        self.assertEqual(clone.count, sketch.count)
        for q in (0, 0.5, 1):
            self.assertEqual(clone.quantile(q), sketch.quantile(q))

    def test_negative_and_zero_values(self):
        for value in (-3, -1, 0, 1, 3):
            self.store.add({"dim": "d", "event_time": BASE, "value": value})
        row = self.store.query("d", start=BASE, end=BASE + 60,
                               quantiles=["p10", "p50", "p90"])
        self.assertEqual(row["sum"], 0.0)
        self.assertAlmostEqual(row["quantiles"]["p50"], 0.0, places=9)
        self.assertLess(row["quantiles"]["p10"], 0.0)
        self.assertGreater(row["quantiles"]["p90"], 0.0)


class ApiTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_module_level_helpers(self):
        add({"dim": "shop", "event_time": "2026-11-15T00:00:00Z", "value": 1.0})
        add_batch([{"dim": "shop", "event_time": "2026-11-15T00:10:00Z", "value": 3.0}])
        row = query("shop", start="2026-11-15T00:00:00Z", end="2026-11-15T01:00:00Z")
        self.assertEqual((row["count"], row["sum"]), (2, 4.0))
        self.assertEqual(len(drilldown("shop", start="2026-11-15T00:00:00Z",
                                       end="2026-11-15T01:00:00Z")), 1)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            add({"dim": "x", "value": 1})
        with self.assertRaises(ValueError):
            add({"dim": "x", "event_time": BASE, "value": float("nan")})


if __name__ == "__main__":
    unittest.main()
