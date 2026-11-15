import bisect
import json
import os
import tempfile
import unittest

from dataprof import (QuantileSketch, configure, export_profile, history,
                      observe, profile, reset)


class IncrementalTest(unittest.TestCase):
    def setUp(self):
        reset()

    def tearDown(self):
        reset()

    def test_mean_updates_incrementally(self):
        observe([{"a": i} for i in range(10)])
        observe([{"a": i} for i in range(10, 25)])
        p = profile("a")
        self.assertAlmostEqual(p["mean"], sum(range(25)) / 25)
        self.assertEqual(p["numeric_count"], 25)

    def test_version_increments_per_batch(self):
        self.assertEqual(observe([{"a": 1}])["version"], 1)
        self.assertEqual(observe([{"a": 2}])["version"], 2)
        self.assertEqual(profile("a")["version"], 2)

    def test_null_rate_accumulates_per_batch(self):
        observe([{"a": 1}, {"a": None}])
        observe([{"b": 5}])  # a 缺失也算空值
        p = profile("a")
        self.assertEqual(p["nulls"], 2)
        self.assertAlmostEqual(p["null_rate"], 2 / 3)

    def test_quantile_sketch_close_to_exact(self):
        data = [float((i * 37) % 503) for i in range(2000)]
        for i in range(0, 2000, 200):
            observe([{"a": v} for v in data[i:i + 200]])
        q50 = profile("a")["quantiles"]["p50"]
        exact = sorted(data)
        rank = bisect.bisect_left(exact, q50)
        self.assertAlmostEqual(rank / len(data), 0.5, delta=0.05)

    def test_sketch_merge(self):
        data = [float((i * 7919) % 1000) for i in range(2000)]
        left, right = QuantileSketch(64), QuantileSketch(64)
        left.update(data[:1000])
        right.update(data[1000:])
        left.merge(right)
        self.assertEqual(left.count, 2000)
        exact = sorted(data)
        rank = bisect.bisect_left(exact, left.quantile(0.5))
        self.assertAlmostEqual(rank / len(data), 0.5, delta=0.06)

    def test_incremental_matches_full_within_tolerance(self):
        data = [float((i * 101) % 997) for i in range(1500)]
        for i in range(0, 1500, 100):
            observe([{"a": v} for v in data[i:i + 100]])
        inc = profile("a")
        reset()
        observe([{"a": v} for v in data])
        full = profile("a")
        self.assertAlmostEqual(inc["mean"], full["mean"], places=9)
        spread = max(data) - min(data)
        for q in ("p25", "p50", "p75", "p95"):
            self.assertAlmostEqual(inc["quantiles"][q], full["quantiles"][q],
                                   delta=spread * 0.02)

    def test_outlier_by_quantile_fence(self):
        rows = [{"a": float(i)} for i in range(1, 101)] + [{"a": 10000.0}]
        observe(rows)
        p = profile("a")
        self.assertEqual(p["outlier_threshold"]["mode"], "iqr")
        self.assertIn(10000.0, p["outliers"])
        self.assertNotIn(50.0, p["outliers"])

    def test_outlier_samples_keep_original_values(self):
        observe([{"a": 1000}])
        outliers = profile("a")["outliers"]
        self.assertEqual(outliers, [1000])
        self.assertIsInstance(outliers[0], int)

    def test_threshold_override_per_field(self):
        configure(field_thresholds={"a": {"absolute_threshold": 5.0}})
        observe([{"a": 10}, {"b": 10}])
        self.assertEqual(profile("a")["outliers"], [10])
        self.assertEqual(profile("b")["outliers"], [])

    def test_type_change_resets_stats(self):
        observe([{"a": 1}, {"a": 2}, {"a": 3}])
        observe([{"a": "x"}, {"a": "y"}])
        p = profile("a")
        self.assertEqual(p["type"], "string")
        self.assertEqual(p["numeric_count"], 0)
        self.assertEqual(p["mean"], 0.0)
        self.assertEqual(p["nulls"], 0)

    def test_persisted_and_pruned_by_version(self):
        with tempfile.TemporaryDirectory() as d:
            configure(storage_dir=d, keep_versions=2)
            observe([{"a": 1}])
            observe([{"a": 2}])
            observe([{"a": 3}])
            files = sorted(os.listdir(d))
            self.assertIn("latest.json", files)
            self.assertIn("profile_v2.json", files)
            self.assertIn("profile_v3.json", files)
            self.assertNotIn("profile_v1.json", files)
            with open(os.path.join(d, "latest.json"), encoding="utf-8") as fh:
                snap = json.load(fh)
            self.assertEqual(snap["version"], 3)
            self.assertEqual(snap["fields"]["a"]["count"], 3)

    def test_history_keeps_recent_versions(self):
        configure(keep_versions=2)
        observe([{"a": 1}])
        observe([{"a": 2}])
        observe([{"a": 3}])
        self.assertEqual([s["version"] for s in history()], [2, 3])

    def test_export_profile(self):
        observe([{"a": 1}, {"a": 2}])
        data = export_profile()
        self.assertIn("a", data["fields"])
        json.dumps(data)  # 可序列化
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.json")
            export_profile("a", path=path)
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            self.assertEqual(loaded["field"], "a")


if __name__ == "__main__":
    unittest.main()
