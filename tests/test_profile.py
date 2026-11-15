import os
import random
import tempfile
import time
import unittest

from profiler2 import Profiler, TDigest


class DigestTest(unittest.TestCase):
    def test_merge_matches_single_digest(self):
        random.seed(3)
        values = [random.gauss(100, 15) for _ in range(20000)]
        whole = TDigest()
        for v in values:
            whole.add(v)

        merged = TDigest()
        for start in range(0, len(values), 700):
            batch = TDigest()
            for v in values[start:start + 700]:
                batch.add(v)
            merged.merge(batch)

        for q in (0.01, 0.25, 0.5, 0.9, 0.99):
            expected = whole.quantile(q)
            got = merged.quantile(q)
            self.assertLess(abs(got - expected) / abs(expected), 0.01, q)

    def test_memory_is_bounded(self):
        digest = TDigest(compression=100)
        for i in range(50000):
            digest.add(i % 9973)
        self.assertLess(len(digest._centroids), 2000)
        self.assertIsNone(digest._raw)

    def test_roundtrip(self):
        digest = TDigest()
        for v in range(1, 5000):
            digest.add(float(v))
        restored = TDigest.from_dict(digest.to_dict())
        self.assertAlmostEqual(restored.quantile(0.5), digest.quantile(0.5))


class IncrementalProfileTest(unittest.TestCase):
    def setUp(self):
        self.profiler = Profiler(autosave=False)

    def tearDown(self):
        self.profiler.close()

    def test_counts_nulls_and_mean(self):
        p = self.profiler
        p.observe([{"v": 10}, {"v": 30}, {"v": None}])
        result = p.profile("v")
        profile = result["profile"]
        self.assertEqual(profile["count"], 3)
        self.assertEqual(profile["null_count"], 1)
        self.assertAlmostEqual(profile["null_ratio"], 1 / 3)
        self.assertAlmostEqual(profile["numeric"]["mean"], 20.0)
        self.assertEqual(result["version"], 1)
        self.assertIsNotNone(result["updated_at"])

    def test_batches_do_not_accumulate_rows(self):
        p = self.profiler
        for start in range(0, 5000, 100):
            p.observe([{"v": i} for i in range(start, start + 100)])
        p.flush(10)
        self.assertFalse(any(name == "_rows" for name in vars(p)))
        self.assertEqual(p.profile("v")["profile"]["count"], 5000)

    def test_observe_does_not_block_on_slow_worker(self):
        p = self.profiler
        original = p._apply_batch

        def slow(rows):
            time.sleep(0.2)
            original(rows)

        p._apply_batch = slow
        p.observe([{"v": 1.0}])
        started = time.monotonic()
        result = p.observe([{"v": 2.0}])
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.1)
        self.assertEqual(result["queued"], 1)
        self.assertTrue(p.flush(10))
        self.assertEqual(p.profile("v")["profile"]["count"], 2)

    def test_close_drains_queue_and_rejects_new_batches(self):
        p = Profiler(autosave=False)
        for _ in range(50):
            p.observe([{"v": 1.0}])
        p.close(10)
        self.assertEqual(p.profile("v", wait=False)["profile"]["count"], 50)
        with self.assertRaises(RuntimeError):
            p.observe([{"v": 2.0}])

    def test_bad_repr_does_not_kill_worker(self):
        class Bad:
            def __repr__(self):
                raise RuntimeError("boom")

        p = self.profiler
        p.observe([{"s": Bad()}, {"s": Bad()}])
        p.flush(10)
        self.assertEqual(p.profile("s")["profile"]["top_values"][0]["value"], "Bad")
        # 后续批次仍能被处理
        p.observe([{"s": Bad()}, {"s": Bad()}, {"s": Bad()}])
        p.flush(10)
        self.assertEqual(p.profile("s")["profile"]["count"], 5)

    def test_incremental_matches_full_scan(self):
        random.seed(11)
        rows = [{"v": random.lognormvariate(4, 1.1)} for _ in range(30000)]
        p = self.profiler
        for start in range(0, len(rows), 2500):
            p.observe(rows[start:start + 2500])
        report = p.verify("v", rows, tolerance=0.01)
        self.assertTrue(report["within_tolerance"], report["differences"])
        self.assertLess(report["differences"]["p50"]["error"], 0.01)

    def test_outliers_follow_quantiles_not_fixed_threshold(self):
        p = self.profiler
        p.observe([{"v": float(i % 100)} for i in range(2000)])
        p.flush(10)
        self.assertEqual(p.outliers("v", only_pending=True), [])

        # 长尾整体抬高时阈值随分位抬升，不应误报
        p.observe([{"v": 10_000 + float(i)} for i in range(500)])
        p.flush(10)
        self.assertEqual(p.profile("v")["profile"]["outlier_count"], 0)

        p.observe([{"v": 10_000_000.0}])
        p.flush(10)
        samples = p.outliers("v")
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["value"], 10_000_000.0)
        self.assertGreater(samples[0]["value"], samples[0]["upper"])

    def test_policy_is_configurable(self):
        p = self.profiler
        p.set_policy("v", upper_quantile=0.5, factor=0.5)
        p.observe([{"v": float(i)} for i in range(100)])
        p.flush(10)
        self.assertGreater(p.profile("v")["profile"]["outlier_count"], 0)

    def test_type_change_resets_statistics(self):
        p = self.profiler
        p.observe([{"v": float(i)} for i in range(200)])
        p.flush(10)
        p.observe([{"v": "text"} for _ in range(20)])
        p.flush(10)
        profile = p.profile("v")["profile"]
        self.assertEqual(profile["category"], "str")
        self.assertEqual(profile["count"], 20)
        self.assertIsNone(profile["numeric"])
        self.assertEqual(profile["version"], 2)
        self.assertEqual(profile["resets"][0]["from"], "number")
        self.assertEqual(profile["resets"][0]["to"], "str")

    def test_single_dirty_value_does_not_reset(self):
        p = self.profiler
        p.observe([{"v": float(i)} for i in range(50)] + [{"v": "oops"}])
        p.flush(10)
        profile = p.profile("v")["profile"]
        self.assertEqual(profile["category"], "number")
        self.assertEqual(profile["type_counts"]["str"], 1)
        self.assertEqual(profile["numeric"]["count"], 50)

    def test_review_outlier(self):
        p = self.profiler
        p.observe([{"v": float(i)} for i in range(100)] + [{"v": 999999.0}])
        p.flush(10)
        sample = p.outliers("v")[0]
        self.assertIsNone(sample["review"])
        reviewed = p.review("v", sample["id"], "false_positive", "unit", "alice")
        self.assertEqual(reviewed["review"]["status"], "false_positive")
        self.assertEqual(reviewed["review"]["reviewer"], "alice")
        self.assertEqual(p.outliers("v", only_pending=True), [])


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "state.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_autosave_reload(self):
        p = Profiler(path=self.path, autosave=True)
        p.observe([{"v": float(i)} for i in range(300)])
        p.flush(10)
        count_before = p.profile("v")["profile"]["count"]
        p.observe([{"v": 500000.0}])
        p.flush(10)
        before = p.profile("v")["profile"]
        self.assertEqual(before["outlier_count"], 1)
        p.close()

        reloaded = Profiler(path=self.path, autosave=False)
        after = reloaded.profile("v")["profile"]
        reloaded.close()
        self.assertEqual(after["count"], before["count"])
        self.assertEqual(after["count"], count_before + 1)
        self.assertAlmostEqual(after["numeric"]["mean"], before["numeric"]["mean"])
        self.assertEqual(after["outlier_count"], 1)

    def test_history_and_replay(self):
        p = Profiler(path=self.path, autosave=True, history_limit=3)
        p.observe([{"v": 1.0}])
        p.flush(10)
        p.observe([{"v": 2.0}])
        p.flush(10)
        versions = [entry["version"] for entry in p.history("v")]
        self.assertEqual(versions, [1, 2])

        # 只读查询历史版本
        historical = p.query("v", version=2)
        self.assertAlmostEqual(historical["profile"]["numeric"]["mean"], 1.5)

        # 回放会把当前状态恢复到指定版本，并丢弃该版本之后的分支
        replayed = p.replay(1)
        self.assertEqual(replayed["version"], 1)
        self.assertAlmostEqual(replayed["fields"]["v"]["profile"]["numeric"]["mean"], 1.0)
        p.close()

    def test_history_is_bounded(self):
        p = Profiler(path=self.path, autosave=False, history_limit=2)
        for i in range(10):
            p.observe([{"v": float(i)}])
        p.flush(10)
        self.assertLessEqual(len(p.history()), 2)
        p.close()

    def test_export_json_and_dict(self):
        p = Profiler(autosave=False)
        p.observe([{"v": 1.0}, {"v": 2.0}])
        p.flush(10)
        text = p.export()
        self.assertIn('"v"', text)
        payload = p.export(fmt="dict")
        self.assertEqual(payload["version"], 1)
        self.assertIn("v", payload["outliers"])
        p.close()


class DefaultApiTest(unittest.TestCase):
    def test_module_level_helpers(self):
        import profiler2

        profiler2.reset_default()
        profiler2.observe([{"v": 1.0}, {"v": 3.0}])
        result = profiler2.profile("v")
        self.assertAlmostEqual(result["profile"]["numeric"]["mean"], 2.0)
        self.assertEqual(result["version"], 1)
        profiler2.reset_default()


if __name__ == "__main__":
    unittest.main()
