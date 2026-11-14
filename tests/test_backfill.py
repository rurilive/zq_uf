import unittest

from metricbackfill import Ledger, MetricStore, apply, plan


class BackfillTest(unittest.TestCase):
    def test_one_shard(self):
        self.assertEqual(len(plan({"start": 1, "end": 9})["shards"]), 1)

    def test_apply_sums(self):
        self.assertEqual(apply(plan({"rows": 3}))["rows"], 3)

    def test_apply_shard_count(self):
        self.assertEqual(apply(plan({"rows": 1}))["shards"], 1)


class ShardPlanTest(unittest.TestCase):
    def test_time_shards(self):
        p = plan({"start": 1, "end": 10, "shard_days": 3})
        self.assertEqual(
            [(s["start"], s["end"]) for s in p["shards"]],
            [(1, 3), (4, 6), (7, 9), (10, 10)],
        )

    def test_date_shards(self):
        p = plan({"start": "2026-01-01", "end": "2026-01-05", "shard_days": 2})
        self.assertEqual(
            [(s["start"], s["end"]) for s in p["shards"]],
            [("2026-01-01", "2026-01-02"), ("2026-01-03", "2026-01-04"), ("2026-01-05", "2026-01-05")],
        )

    def test_dim_shards(self):
        p = plan({"start": 1, "end": 2, "dims": {"region": ["cn", "us"]}})
        self.assertEqual(len(p["shards"]), 2)
        self.assertEqual({s["dims"]["region"] for s in p["shards"]}, {"cn", "us"})

    def test_time_and_dim_shards(self):
        p = plan({"start": 1, "end": 4, "shard_days": 2, "dims": {"region": ["cn", "us"]}})
        self.assertEqual(len(p["shards"]), 4)

    def test_rows_split_across_shards(self):
        p = plan({"start": 1, "end": 4, "shard_days": 2, "rows": 5})
        self.assertEqual(sum(s["rows"] for s in p["shards"]), 5)
        self.assertEqual(apply(p)["rows"], 5)


class UndoRewriteTest(unittest.TestCase):
    def test_undo_then_rewrite(self):
        store = MetricStore([
            {"time": 1, "dims": {}, "value": 100},
            {"time": 2, "dims": {}, "value": 200},
        ])
        source = MetricStore([
            {"time": 1, "dims": {}, "value": 7},
            {"time": 2, "dims": {}, "value": 8},
        ])
        result = apply(plan({"start": 1, "end": 2}), store=store, source=source)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(sorted(r["value"] for r in store.rows), [7, 8])

    def test_idempotent_reapply(self):
        source = MetricStore([
            {"time": 1, "dims": {}, "value": 7},
            {"time": 2, "dims": {}, "value": 8},
        ])
        store = MetricStore()
        p = plan({"start": 1, "end": 2})
        apply(p, store=store, source=source)
        once = sorted(r["value"] for r in store.rows)
        apply(p, store=store, source=source)
        self.assertEqual(sorted(r["value"] for r in store.rows), once)
        self.assertEqual(len(store.rows), 2)

    def test_undo_only_touches_shard_range(self):
        store = MetricStore([
            {"time": 1, "dims": {}, "value": 100},
            {"time": 9, "dims": {}, "value": 900},
        ])
        source = MetricStore([{"time": 1, "dims": {}, "value": 7}])
        apply(plan({"start": 1, "end": 2}), store=store, source=source)
        by_time = {r["time"]: r["value"] for r in store.rows}
        self.assertEqual(by_time, {1: 7, 9: 900})


class ReconcileTest(unittest.TestCase):
    def test_reconcile_pass_within_threshold(self):
        source = MetricStore([{"time": 1, "dims": {}, "value": 12}])
        store = MetricStore([{"time": 1, "dims": {}, "value": 10}])
        result = apply(plan({"start": 1, "end": 1, "threshold": 5}), store=store, source=source)
        self.assertFalse(result["stopped"])
        self.assertEqual(result["results"][0]["status"], "applied")
        self.assertEqual(result["results"][0]["value_diff"], 2)

    def test_reconcile_stop_keeps_applied_shards(self):
        source = MetricStore([
            {"time": 1, "dims": {}, "value": 10},
            {"time": 2, "dims": {}, "value": 10},
        ])
        store = MetricStore([
            {"time": 1, "dims": {}, "value": 10},   # 与源一致
            {"time": 2, "dims": {}, "value": 999},  # 差异超阈值
        ])
        p = plan({"start": 1, "end": 2, "shard_days": 1, "threshold": 5})
        result = apply(p, store=store, source=source)
        self.assertTrue(result["stopped"])
        self.assertIn("阈值", result["stop_reason"])
        self.assertEqual([r["status"] for r in result["results"]], ["applied", "rejected"])
        # 已回填分片保留，超限分片回滚到原值
        by_time = {r["time"]: r["value"] for r in store.rows}
        self.assertEqual(by_time, {1: 10, 2: 999})

    def test_reconcile_diff_per_shard(self):
        source = MetricStore([
            {"time": 1, "dims": {"region": "cn"}, "value": 1},
            {"time": 1, "dims": {"region": "us"}, "value": 2},
        ])
        store = MetricStore([
            {"time": 1, "dims": {"region": "cn"}, "value": 1},
            {"time": 1, "dims": {"region": "us"}, "value": 5},
        ])
        p = plan({"start": 1, "end": 1, "dims": {"region": ["cn", "us"]}, "threshold": 10})
        result = apply(p, store=store, source=source)
        diffs = {r["dims"]["region"]: r["value_diff"] for r in result["results"]}
        self.assertEqual(diffs, {"cn": 0, "us": -3})


class LedgerTest(unittest.TestCase):
    def test_records_query_by_range(self):
        ledger = Ledger()
        source = MetricStore([{"time": t, "dims": {}, "value": 1} for t in (1, 2, 3)])
        apply(
            plan({"start": 1, "end": 3, "shard_days": 1}),
            store=MetricStore(), source=source, ledger=ledger,
        )
        self.assertEqual(len(ledger.records), 3)
        hit = ledger.query(start=2, end=3)
        self.assertEqual({r["start"] for r in hit}, {2, 3})
        self.assertEqual(len(ledger.query(start=9, end=10)), 0)


if __name__ == "__main__":
    unittest.main()
