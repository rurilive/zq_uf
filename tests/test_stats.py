import os
import shutil
import tempfile
import unittest

from partitionstats import hotspots, invalidate, prune, query, stats


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for i in range(2):
            open(os.path.join(self.dir, "p%d.jsonl" % i), "w").write("{}\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_counts_files(self):
        self.assertEqual(stats(self.dir)["files"], 2)

    def test_missing_dataset(self):
        self.assertEqual(stats(os.path.join(self.dir, "nope"))["files"], 0)

    def test_prune_shape(self):
        self.assertEqual(set(prune({})), {"scan", "skip"})


def _write_month(root, month, days):
    path = os.path.join(root, "%s.jsonl" % month)
    with open(path, "w") as fh:
        for d in days:
            fh.write('{"ts": "%s-%02dT00:00:00"}\n' % (month, d))
    return path


class MonthlyTest(unittest.TestCase):
    """跨月分区：01 月 2 行，02 月 3 行，03 月 1 行。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        _write_month(self.dir, "2024-01", (5, 15))
        _write_month(self.dir, "2024-02", (1, 10, 20))
        _write_month(self.dir, "2024-03", (28,))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class RegisterTest(MonthlyTest):
    def test_partition_stats_registered(self):
        parts = stats(self.dir)["partitions"]
        self.assertEqual(parts["2024-01.jsonl"]["rows"], 2)
        self.assertEqual(parts["2024-02.jsonl"]["rows"], 3)
        self.assertGreater(parts["2024-01.jsonl"]["size"], 0)

    def test_time_range_registered(self):
        p = stats(self.dir)["partitions"]["2024-01.jsonl"]
        self.assertLess(p["tmin"], p["tmax"])

    def test_aggregate_rows_and_bytes(self):
        s = stats(self.dir)
        self.assertEqual(s["rows"], 6)
        self.assertEqual(s["files"], 3)
        self.assertGreater(s["bytes"], 0)


class CacheTest(MonthlyTest):
    def test_stats_cached_until_invalidated(self):
        self.assertEqual(stats(self.dir)["files"], 3)
        _write_month(self.dir, "2024-04", (1,))
        self.assertEqual(stats(self.dir)["files"], 3)  # 缓存未失效
        invalidate(self.dir)
        self.assertEqual(stats(self.dir)["files"], 4)

    def test_delete_updates_after_invalidate(self):
        stats(self.dir)
        os.remove(os.path.join(self.dir, "2024-01.jsonl"))
        invalidate(self.dir)
        s = stats(self.dir)
        self.assertEqual(s["files"], 2)
        self.assertEqual(s["rows"], 4)


class PruneTest(MonthlyTest):
    def test_prune_by_time_range(self):
        result = prune({"dataset": self.dir, "start": "2024-02-01", "end": "2024-02-29"})
        self.assertEqual(result["scan"], ["2024-02.jsonl"])
        self.assertEqual(result["skip"], ["2024-01.jsonl", "2024-03.jsonl"])

    def test_prune_open_range_scans_all(self):
        result = prune({"dataset": self.dir})
        self.assertEqual(len(result["scan"]), 3)
        self.assertEqual(result["skip"], [])

    def test_partition_without_time_range_always_scanned(self):
        with open(os.path.join(self.dir, "unknown.jsonl"), "w") as fh:
            fh.write("{}\n")
        invalidate(self.dir)
        result = prune({"dataset": self.dir, "start": "2024-02-01", "end": "2024-02-29"})
        self.assertIn("unknown.jsonl", result["scan"])


class QueryTest(MonthlyTest):
    def test_query_outputs_scanned_skipped_elapsed(self):
        result = query(self.dir, {"start": "2024-02-01", "end": "2024-02-29"})
        self.assertEqual(result["scanned"], ["2024-02.jsonl"])
        self.assertEqual(result["skipped"], ["2024-01.jsonl", "2024-03.jsonl"])
        self.assertGreaterEqual(result["elapsed"], 0.0)


class HotspotTest(MonthlyTest):
    def test_hotspots_sorted_by_hits_then_size(self):
        query(self.dir, {"start": "2024-02-01", "end": "2024-02-29"})
        query(self.dir, {"start": "2024-02-01", "end": "2024-02-29"})
        top = hotspots(self.dir)[0]
        self.assertEqual(top["partition"], "2024-02.jsonl")
        self.assertEqual(top["hits"], 2)
