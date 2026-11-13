import unittest

from datasetcache import (
    TTL,
    clear,
    get,
    invalidate,
    preheat,
    put,
    register_dataset,
    report,
    set_version,
)


class VersionInvalidationTest(unittest.TestCase):
    def setUp(self):
        clear()

    def test_version_change_invalidates(self):
        register_dataset("orders", version=1)
        put("orders:1", "v1", dataset="orders", now=100)
        self.assertEqual(get("orders:1", now=101), "v1")
        self.assertEqual(invalidate("orders", version=2), 1)
        self.assertIsNone(get("orders:1", now=101))

    def test_same_version_is_noop(self):
        register_dataset("orders", version=1)
        put("orders:1", "v1", dataset="orders", now=100)
        self.assertEqual(invalidate("orders", version=1), 0)
        self.assertEqual(get("orders:1", now=101), "v1")

    def test_lazy_version_miss_reason(self):
        register_dataset("orders", version=1)
        put("orders:1", "v1", dataset="orders", now=100)
        set_version("orders", 2)
        self.assertIsNone(get("orders:1", now=101))
        self.assertEqual(report()["miss_reasons"]["version"], 1)


class DependencyInvalidationTest(unittest.TestCase):
    def setUp(self):
        clear()

    def test_downstream_invalidated_by_lineage(self):
        register_dataset("dim_user", version=1)
        register_dataset("fact_orders", version=1, depends_on=["dim_user"])
        register_dataset("fact_logistics", version=1)
        put("dim:1", 1, dataset="dim_user", now=100)
        put("fact:1", 2, dataset="fact_orders", now=100)
        put("log:1", 3, dataset="fact_logistics", now=100)
        self.assertEqual(invalidate("dim_user", version=2), 2)
        self.assertIsNone(get("dim:1", now=101))
        self.assertIsNone(get("fact:1", now=101))
        self.assertEqual(get("log:1", now=101), 3)

    def test_entry_level_depends(self):
        put("join:1", 1, depends=["dim_user"], now=100)
        put("other:1", 2, depends=["dim_region"], now=100)
        self.assertEqual(invalidate("dim_user", version=2), 1)
        self.assertIsNone(get("join:1", now=101))
        self.assertEqual(get("other:1", now=101), 2)


class BatchRateLimitTest(unittest.TestCase):
    def setUp(self):
        clear()

    def test_batched_rate_limited_invalidation(self):
        register_dataset("orders", version=1)
        for i in range(5):
            put("orders:%d" % i, i, dataset="orders", now=100)
        slept = []
        count = invalidate("orders", version=2, batch_size=2, rate=4,
                           sleep=slept.append)
        self.assertEqual(count, 5)
        self.assertEqual(len(slept), 2)  # 3 批之间停 2 次
        self.assertAlmostEqual(sum(slept), 2 / 4 + 2 / 4)
        last = report()["last_invalidation"]
        self.assertEqual(last["batches"], 3)
        self.assertGreaterEqual(last["elapsed"], 0)


class PreheatFallbackTest(unittest.TestCase):
    def setUp(self):
        clear()

    def test_preheat_counts_refetch(self):
        register_dataset("orders", version=1)
        count = preheat(["a", "b"], lambda k: k.upper(), dataset="orders", now=100)
        self.assertEqual(count, 2)
        self.assertEqual(get("a", now=101), "A")
        self.assertEqual(report()["refetched"], 2)

    def test_stale_fallback_when_origin_down(self):
        register_dataset("orders", version=1)
        put("k", "old", dataset="orders", now=100)
        set_version("orders", 2)

        def boom(key):
            raise RuntimeError("origin down")

        self.assertEqual(get("k", now=101, loader=boom, allow_stale=True), "old")
        self.assertEqual(report()["stale_served"], 1)

    def test_loader_refreshes_on_miss(self):
        put("k", "old", now=100)
        self.assertEqual(get("k", now=100 + TTL + 1, loader=lambda k: "new"), "new")
        self.assertEqual(get("k", now=100 + TTL + 2), "new")
        self.assertEqual(report()["refetched"], 1)


class ReportTest(unittest.TestCase):
    def setUp(self):
        clear()

    def test_report_fields(self):
        register_dataset("orders", version=1)
        put("k", "v", dataset="orders", now=100)
        get("nope", now=100)
        get("k", now=100 + TTL + 1)
        invalidate("orders", version=2)
        r = report()
        self.assertEqual(r["invalidated"], 1)
        self.assertEqual(r["miss_reasons"], {"missing": 1, "expired": 1})
        self.assertEqual(r["last_invalidation"]["dataset"], "orders")
        self.assertEqual(r["last_invalidation"]["invalidated"], 1)
        self.assertIn("elapsed", r["last_invalidation"])
        self.assertIn("refetched", r)


if __name__ == "__main__":
    unittest.main()
