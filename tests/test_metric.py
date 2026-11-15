import json
import unittest

from metricmerge import (
    add,
    backfill_queue,
    merge_sketches,
    recompute,
    reset,
    windows,
)


class MetricTest(unittest.TestCase):
    def test_add_accumulates(self):
        add({"window": "w1", "value": 2})
        self.assertEqual(add({"window": "w1", "value": 3})["sum"], 5.0)

    def test_add_counts(self):
        add({"window": "w2", "value": 1})
        self.assertEqual(add({"window": "w2", "value": 1})["count"], 2)

    def test_recompute_does_nothing(self):
        add({"window": "w3", "value": 1})
        self.assertFalse(recompute("w3")["recomputed"])


class RecomputeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def _seed(self):
        add({"event_time": "2026-01-01T08:00:00Z", "value": 1.5, "id": "e1"})
        add({"event_time": "2026-01-01T09:00:00Z", "value": 2.5, "id": "e2"})
        add({"event_time": "2026-01-02T10:00:00Z", "value": 4.0, "id": "e3"})
        return "2026-01-01T00:00:00+00:00"

    def test_window_aligned_to_event_time(self):
        reset()
        result = add({"event_time": "2026-01-01T13:00:00Z", "value": 1})
        self.assertEqual(result["window"], "2026-01-01T00:00:00+00:00")

    def test_late_triggers_recompute(self):
        key = self._seed()
        result = add({"event_time": "2026-01-01T23:00:00Z", "value": 10.0, "id": "e4"})
        self.assertTrue(result["late"])
        self.assertTrue(result["recomputed"])
        self.assertEqual(result["sum"], 14.0)
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["version"], 1)
        self.assertEqual(recompute(key)["sum"], 14.0)

    def test_recompute_undoes_then_rewrites(self):
        self._seed()
        add({"event_time": "2026-01-01T23:00:00Z", "value": 10.0, "id": "e4"})
        result = add({"event_time": "2026-01-01T23:30:00Z", "value": 1.0, "id": "e5"})
        # 撤销旧版本（sum=14）再写新版本（sum=15），不是 14+15 重复累加
        self.assertEqual(result["retracted"]["sum"], 14.0)
        self.assertEqual(result["sum"], 15.0)
        self.assertEqual(result["version"], 2)

    def test_duplicate_late_not_double_counted(self):
        self._seed()
        add({"event_time": "2026-01-01T23:00:00Z", "value": 10.0, "id": "e4"})
        dup = add({"event_time": "2026-01-01T23:00:00Z", "value": 10.0, "id": "e4"})
        self.assertTrue(dup["deduplicated"])
        self.assertEqual(dup["count"], 3)
        self.assertEqual(dup["sum"], 14.0)

    def test_recompute_idempotent(self):
        key = self._seed()
        add({"event_time": "2026-01-01T23:00:00Z", "value": 10.0, "id": "e4"})
        first = recompute(key)
        second = recompute(key)
        self.assertFalse(first["recomputed"])  # add 已触发重算，窗口干净
        self.assertFalse(second["recomputed"])
        self.assertEqual(first, second)

    def test_cardinality_reconciled(self):
        self._seed()
        result = add({"event_time": "2026-01-01T23:00:00Z", "value": 10.0, "id": "e4"})
        cardinality = result["cardinality"]
        self.assertTrue(cardinality["ok"])
        self.assertEqual(cardinality["events"], 3)
        self.assertEqual(cardinality["count"], 3)
        self.assertEqual(cardinality["sketch_weight"], 3)

    def test_sketch_merge_and_cardinality(self):
        reset()
        for i, v in enumerate([1.0, 2.0, 3.0]):
            add({"event_time": "2026-01-01T0%d:00:00Z" % i, "value": v, "id": "a%d" % i})
        for i, v in enumerate([4.0, 5.0, 6.0]):
            add({"event_time": "2026-01-02T0%d:00:00Z" % i, "value": v, "id": "b%d" % i})
        sketches = [w["sketch"] for w in windows()]
        self.assertEqual(len(sketches), 2)
        merged = merge_sketches(*sketches)
        self.assertTrue(merged["cardinality"]["ok"])
        self.assertEqual(merged["cardinality"]["merged"], 6)
        self.assertIn(merged["quantiles"]["p50"], (3.0, 4.0))

    def test_sketch_state_serializable(self):
        self._seed()
        sketch = windows()[0]["sketch"]
        self.assertEqual(json.loads(json.dumps(sketch)), sketch)

    def test_sketch_compaction_keeps_cardinality(self):
        reset()
        for i in range(300):
            add({"event_time": "2026-01-01T00:00:%02dZ" % (i % 60), "value": float(i), "id": "c%d" % i})
        sketch = windows()[0]["sketch"]
        self.assertLessEqual(len(sketch["items"]), sketch["capacity"])
        merged = merge_sketches(sketch)
        self.assertEqual(merged["cardinality"]["merged"], 300)

    def test_expired_late_goes_to_backfill_queue(self):
        self._seed()
        result = add({"event_time": "2025-12-01T00:00:00Z", "value": 99.0, "id": "old"})
        self.assertTrue(result["backfilled"])
        queue = backfill_queue()
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["reason"], "retention_expired")
        self.assertEqual(queue[0]["metric"]["id"], "old")
