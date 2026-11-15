import unittest

from smallfile import (cleanup_history, compact, plan, retry_failed, rollback)


def make_part(name, sizes, active_reads=0, fail_merge=False):
    return {
        "name": name,
        "files": [{"path": "{}/f{}.dat".format(name, i), "size": s}
                  for i, s in enumerate(sizes)],
        "manifest": ["{}/f{}.dat".format(name, i) for i in range(len(sizes))],
        "active_reads": active_reads,
        "fail_merge": fail_merge,
        "history": [],
    }


class PlanTest(unittest.TestCase):
    def test_small_files_selected(self):
        ds = {"partitions": [make_part("p1", [10, 10, 10])]}
        self.assertEqual(plan(ds)["selected"], ["p1"])

    def test_large_files_skipped(self):
        big = 64 * 1024 * 1024
        ds = {"partitions": [make_part("p1", [big, big])]}
        r = plan(ds)
        self.assertEqual(r["selected"], [])
        self.assertEqual(r["skipped"][0]["reason"], "files-large-enough")

    def test_active_reads_skipped(self):
        ds = {"partitions": [make_part("p1", [10, 10], active_reads=2)]}
        r = plan(ds)
        self.assertEqual(r["selected"], [])
        self.assertEqual(r["skipped"][0]["reason"], "active-reads")

    def test_too_few_files_skipped(self):
        ds = {"partitions": [make_part("p1", [10])]}
        self.assertEqual(plan(ds)["skipped"][0]["reason"], "too-few-files")

    def test_stats_reported(self):
        ds = {"partitions": [make_part("p1", [10, 20, 30])]}
        self.assertEqual(plan(ds)["stats"]["p1"],
                         {"count": 3, "bytes": 60, "avg_bytes": 20})

    def test_trial_only_one_partition(self):
        ds = {"partitions": [make_part("small", [10, 10]),
                             make_part("other", [10, 10])]}
        r = plan(ds, only=["small"])
        self.assertEqual(r["selected"], ["small"])
        self.assertEqual(r["skipped"][0]["reason"], "not-in-trial")


class CompactTest(unittest.TestCase):
    def test_atomic_replace_and_manifest_synced(self):
        part = make_part("p1", [10, 20, 30])
        r = compact(plan({"partitions": [part]}))
        self.assertEqual(r["merged"], ["p1"])
        self.assertEqual(len(part["files"]), 1)
        self.assertEqual(part["files"][0]["size"], 60)
        # 清单同步指向新文件
        self.assertEqual(part["manifest"], [part["files"][0]["path"]])
        # 原文件进历史，可回退
        self.assertEqual(len(part["history"]), 1)
        self.assertEqual(len(part["history"][0]["files"]), 3)

    def test_file_counts_before_after(self):
        part = make_part("p1", [10, 10, 10, 10])
        r = compact(plan({"partitions": [part]}))
        self.assertEqual(r["file_counts"]["p1"], {"before": 4, "after": 1})

    def test_active_read_at_compact_time_skipped(self):
        part = make_part("p1", [10, 10])
        p = plan({"partitions": [part]})
        part["active_reads"] = 1  # plan 之后来了新查询
        r = compact(p)
        self.assertEqual(r["merged"], [])
        self.assertEqual(r["skipped"][-1]["reason"], "active-reads")
        self.assertEqual(len(part["files"]), 2)

    def test_failure_keeps_originals(self):
        part = make_part("p1", [10, 10], fail_merge=True)
        r = compact(plan({"partitions": [part]}))
        self.assertEqual(r["failed"][0]["partition"], "p1")
        self.assertEqual(len(part["files"]), 2)  # 原文件保留
        self.assertEqual(len(part["manifest"]), 2)  # 清单不动

    def test_result_lists(self):
        ok = make_part("ok", [10, 10])
        busy = make_part("busy", [10, 10], active_reads=1)
        bad = make_part("bad", [10, 10], fail_merge=True)
        r = compact(plan({"partitions": [ok, busy, bad]}))
        self.assertEqual(r["merged"], ["ok"])
        self.assertEqual([s["partition"] for s in r["skipped"]], ["busy"])
        self.assertEqual([f["partition"] for f in r["failed"]], ["bad"])
        self.assertEqual(r["replaced"][0]["partition"], "ok")
        self.assertEqual(r["replaced"][0]["old_files"], 2)


class RetryRollbackHistoryTest(unittest.TestCase):
    def test_retry_failed_batch(self):
        part = make_part("p1", [10, 10], fail_merge=True)
        ds = {"partitions": [part]}
        r1 = compact(plan(ds))
        self.assertEqual(len(r1["failed"]), 1)
        part["fail_merge"] = False  # 故障修复后单独重试
        r2 = retry_failed(ds, r1["failed"])
        self.assertEqual(r2["merged"], ["p1"])
        self.assertEqual(len(part["files"]), 1)

    def test_rollback_restores_files_and_manifest(self):
        part = make_part("p1", [10, 20])
        old_manifest = list(part["manifest"])
        r = compact(plan({"partitions": [part]}))
        self.assertTrue(rollback(part, r["batch_id"]))
        self.assertEqual(len(part["files"]), 2)
        self.assertEqual(part["manifest"], old_manifest)

    def test_history_retention_cleanup(self):
        part = make_part("p1", [10, 10])
        compact(plan({"partitions": [part]}), now=1000)
        # 保留期内不清理
        self.assertEqual(cleanup_history([part], retention_seconds=100,
                                         now=1050), [])
        # 过期清理
        removed = cleanup_history([part], retention_seconds=100, now=1200)
        self.assertEqual(removed[0]["partition"], "p1")
        self.assertEqual(part["history"], [])


if __name__ == "__main__":
    unittest.main()
