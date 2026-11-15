import importlib
import json
import os
import tempfile
import unittest

from partclone import (
    MAX_RETRIES,
    CloneError,
    CloneInterrupted,
    Partition,
    VerifyError,
    audit_log,
    clone,
    is_visible,
    load_checkpoint,
    query,
    reset,
    verify,
)

module = importlib.import_module("partclone.clone")


class CloneTest(unittest.TestCase):
    def test_clone_copies_rows(self):
        self.assertEqual(clone({"tag": "t1", "rows": 5})["copied"], 5)

    def test_clone_has_no_snapshot(self):
        self.assertIsNone(clone({"tag": "t2", "rows": 1})["snapshot"])

    def test_verify_always_matches(self):
        clone({"tag": "t3", "rows": 2})
        self.assertTrue(verify("t3")["matched"])


def _rows(count):
    return [{"id": index, "value": index * 10} for index in range(count)]


class _WritesDuringSnapshot(Partition):
    """取快照后立刻写源分区，模拟克隆期间的并发写。"""

    def snapshot(self):
        snap = super().snapshot()
        self.append({"id": 999, "value": -1})
        self.delete(0)
        return snap


class _ObservesVisibility(Partition):
    """复制过程中记录 query() 结果：克隆未完成时目标必须不可见。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen = []

    def after_row(self, index):
        self.seen.append(query(self.tag))


class SnapshotCloneTest(unittest.TestCase):
    def setUp(self):
        reset()
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = self._tmp.name

    def tearDown(self):
        reset()
        self._tmp.cleanup()

    def test_snapshot_records_version_and_row_count(self):
        source = Partition("orders", rows=_rows(3), version=7)
        result = clone(source, state_dir=self.state_dir)
        snapshot = result["snapshot"]
        self.assertEqual(snapshot["version"], 7)
        self.assertEqual(snapshot["rows"], 3)
        self.assertTrue(snapshot["read_only"])
        self.assertEqual(snapshot["checksum"], result["verify"]["checksum_source"])
        self.assertEqual(result["copied"], 3)
        self.assertEqual(query("orders"), _rows(3))

    def test_snapshot_isolated_from_writes_during_clone(self):
        source = _WritesDuringSnapshot("live", rows=_rows(4))
        result = clone(source, state_dir=self.state_dir)
        # 克隆的是快照：快照之后的写入/删除不进目标
        self.assertEqual(result["snapshot"]["rows"], 4)
        self.assertEqual(result["snapshot"]["version"], 0)
        self.assertEqual(query("live"), _rows(4))
        # 源分区自身仍保留并发写的结果
        self.assertEqual([row["id"] for row in source.rows], [1, 2, 3, 999])
        self.assertTrue(result["verify"]["ok"])

    def test_clone_reads_source_without_writing(self):
        source = Partition("readonly", rows=_rows(5), version=3)
        before_rows = json.dumps(source.rows, sort_keys=True)
        clone(source, state_dir=self.state_dir)
        self.assertEqual(source.writes, 0)
        self.assertEqual(source.version, 3)
        self.assertEqual(json.dumps(source.rows, sort_keys=True), before_rows)

    def test_target_invisible_until_clone_completes(self):
        source = _ObservesVisibility("visibility", rows=_rows(5))
        result = clone(source, state_dir=self.state_dir)
        # 复制期间查询一律看不到目标，完成后才可见
        self.assertEqual(source.seen, [None] * 5)
        self.assertTrue(result["visible"])
        self.assertTrue(is_visible("visibility"))
        self.assertEqual(query("visibility"), _rows(5))


class FailureCleanupTest(unittest.TestCase):
    def setUp(self):
        reset()
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = self._tmp.name

    def tearDown(self):
        reset()
        self._tmp.cleanup()

    def test_failure_cleans_target_and_keeps_checkpoint(self):
        source = Partition("fail", rows=_rows(10), fail_after=4, fail_times=99)
        with self.assertRaises(CloneError) as caught:
            clone(source, state_dir=self.state_dir, max_retries=2)
        # 目标半成品已清理，查询不可见
        self.assertIsNone(query("fail"))
        self.assertFalse(is_visible("fail"))
        # 断点落盘：快照版本、已复制行数保留
        checkpoint = load_checkpoint("fail", state_dir=self.state_dir)
        self.assertEqual(checkpoint["phase"], "copying")
        self.assertEqual(checkpoint["snapshot_version"], 0)
        self.assertEqual(checkpoint["row_count"], 10)
        self.assertEqual(checkpoint["retries"], 2)
        self.assertGreater(checkpoint["offset"], 0)
        self.assertEqual(caught.exception.retries, 2)
        self.assertTrue(os.path.exists(os.path.join(
            self.state_dir, "fail", "snapshot.json")))
        events = [entry["event"] for entry in
                  audit_log("fail", state_dir=self.state_dir)]
        self.assertIn("cleanup", events)
        self.assertIn("give_up", events)

    def test_retry_is_limited(self):
        source = Partition("bounded", rows=_rows(5), fail_after=0, fail_times=99)
        with self.assertRaises(CloneError):
            clone(source, state_dir=self.state_dir)
        checkpoint = load_checkpoint("bounded", state_dir=self.state_dir)
        self.assertEqual(checkpoint["retries"], MAX_RETRIES)


class ResumeTest(unittest.TestCase):
    def setUp(self):
        reset()
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = self._tmp.name

    def tearDown(self):
        reset()
        self._tmp.cleanup()

    def test_transient_failure_retries_from_breakpoint(self):
        source = Partition("flaky", rows=_rows(8), fail_after=3, fail_times=1)
        result = clone(source, state_dir=self.state_dir)
        self.assertEqual(result["retries"], 1)
        self.assertTrue(result["resumed"])
        self.assertEqual(result["resumed_from"], 4)
        self.assertEqual(result["copied"], 8)
        self.assertEqual(query("flaky"), _rows(8))
        self.assertEqual(source.snapshot_calls, 1)
        self.assertTrue(result["verify"]["ok"])

    def test_restart_after_exhausted_retries_resumes_from_disk(self):
        source = Partition("crash", rows=_rows(9), fail_after=5, fail_times=99)
        with self.assertRaises(CloneError):
            clone(source, state_dir=self.state_dir, max_retries=1)
        offset = load_checkpoint("crash", state_dir=self.state_dir)["offset"]

        # 新进程：内存状态清空，只剩落盘断点
        reset()
        healthy = Partition("crash", rows=_rows(9))
        result = clone(healthy, state_dir=self.state_dir)
        self.assertTrue(result["resumed"])
        self.assertEqual(result["resumed_from"], offset)
        self.assertEqual(result["retries"], 0)
        self.assertEqual(healthy.snapshot_calls, 0)  # 复用落盘快照
        self.assertEqual(result["snapshot"]["version"], 0)
        self.assertEqual(query("crash"), _rows(9))
        self.assertTrue(result["verify"]["ok"])

    def test_completed_checkpoint_is_not_reused(self):
        source = Partition("done", rows=_rows(3))
        clone(source, state_dir=self.state_dir)
        reset()
        source2 = Partition("done", rows=_rows(5))
        result = clone(source2, state_dir=self.state_dir)
        self.assertFalse(result["resumed"])
        self.assertEqual(source2.snapshot_calls, 1)
        self.assertEqual(result["copied"], 5)


class VerifyTest(unittest.TestCase):
    def setUp(self):
        reset()
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = self._tmp.name

    def tearDown(self):
        reset()
        self._tmp.cleanup()

    def test_verify_compares_rows_and_checksum(self):
        source = Partition("check", rows=_rows(6))
        result = clone(source, state_dir=self.state_dir)
        self.assertTrue(result["verify"]["ok"])
        report = verify("check")
        self.assertTrue(report["matched"])
        self.assertEqual(report["rows_source"], 6)
        self.assertEqual(report["rows_target"], 6)
        self.assertEqual(report["checksum_source"], report["checksum_target"])
        self.assertEqual(report["rows"], 6)

    def test_checksum_mismatch_aborts_and_keeps_counts(self):
        source = Partition("corrupt", rows=_rows(4), corrupt_index=2)
        with self.assertRaises(VerifyError) as caught:
            clone(source, state_dir=self.state_dir)
        error = caught.exception
        self.assertEqual(error.rows_source, 4)
        self.assertEqual(error.rows_target, 4)
        self.assertNotEqual(error.checksum_source, error.checksum_target)
        self.assertEqual(error.result["reason"], "checksum")
        # 校验不一致：中止，目标不可见
        self.assertIsNone(query("corrupt"))
        checkpoint = load_checkpoint("corrupt", state_dir=self.state_dir)
        self.assertEqual(checkpoint["phase"], "verify_failed")
        entries = audit_log("corrupt", state_dir=self.state_dir)
        failed = [entry for entry in entries if entry["event"] == "verify_failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["rows_source"], 4)
        self.assertEqual(failed[0]["rows_target"], 4)

    def test_row_count_mismatch_reported(self):
        source = Partition("short", rows=_rows(3), drop_index=1)
        with self.assertRaises(VerifyError) as caught:
            clone(source, state_dir=self.state_dir)
        self.assertEqual(caught.exception.rows_source, 3)
        self.assertEqual(caught.exception.rows_target, 2)
        self.assertEqual(caught.exception.result["reason"], "rows")

    def test_verify_result_is_audited(self):
        clone(Partition("audit", rows=_rows(2)), state_dir=self.state_dir)
        verify("audit")
        entries = audit_log("audit", state_dir=self.state_dir)
        self.assertEqual(
            [entry["event"] for entry in entries][-1], "verify")
        self.assertTrue(entries[-1]["ok"])

    def test_verify_unknown_tag_is_not_matched(self):
        self.assertFalse(verify("missing")["matched"])


if __name__ == "__main__":
    unittest.main()
