import importlib
import json
import os
import tempfile
import unittest

from tablecopy import CopyError, Table, copy, verify

module = importlib.import_module("tablecopy.copy")


class CopyTest(unittest.TestCase):
    def setUp(self):
        module._source.clear()
        module._target.clear()

    def test_copy_rows(self):
        self.assertEqual(copy([{"id": 1}, {"id": 2}], "dst")["rows"], 2)

    def test_copy_replaces(self):
        copy([{"id": 1}], "dst")
        copy([{"id": 2}], "dst")
        self.assertEqual(list(module._target), [2])

    def test_verify_rows(self):
        module._source.update({1: {"id": 1}})
        copy([{"id": 1}], "dst")
        self.assertTrue(verify()["ok"])


class _WriterTable(Table):
    """快照位点确定后立刻写入，模拟复制期间的并发写。"""

    def snapshot(self):
        lsn, rows = super().snapshot()
        self.write({"id": 99})
        self.delete(1)
        return lsn, rows


class _FlakyChangeTable(Table):
    """拉取增量时前 fail_times 次抛错，模拟复制中途失败。"""

    def __init__(self, rows, fail_times):
        super().__init__(rows)
        self.fail_times = fail_times
        self.snapshot_calls = 0
        self.change_calls = 0

    def snapshot(self):
        self.snapshot_calls += 1
        return super().snapshot()

    def changes_since(self, lsn):
        self.change_calls += 1
        if self.change_calls <= self.fail_times:
            raise RuntimeError("boom")
        return super().changes_since(lsn)


class SnapshotCopyTest(unittest.TestCase):
    def setUp(self):
        module._source.clear()
        module._target.clear()
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["TABLECOPY_STATE_DIR"] = self._tmp.name

    def tearDown(self):
        os.environ.pop("TABLECOPY_STATE_DIR", None)
        self._tmp.cleanup()

    def _checkpoint(self, dst):
        path = os.path.join(self._tmp.name, dst, "checkpoint.json")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_snapshot_copy_persists_position(self):
        table = Table([{"id": 1}, {"id": 2}])
        result = copy(table, "snap")
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["snapshot_lsn"], 0)
        self.assertEqual(result["incremental"], 0)
        self.assertTrue(result["verify"]["ok"])
        checkpoint = self._checkpoint("snap")
        self.assertEqual(checkpoint["snapshot_lsn"], 0)
        self.assertEqual(checkpoint["phase"], "done")

    def test_incremental_catchup(self):
        table = _WriterTable([{"id": 1}, {"id": 2}])
        result = copy(table, "incr")
        self.assertEqual(result["incremental"], 2)
        self.assertEqual(sorted(module._target), [2, 99])
        self.assertTrue(result["verify"]["ok"])
        increments = os.path.join(self._tmp.name, "incr", "increments.jsonl")
        with open(increments, encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().splitlines()), 2)

    def test_retry_from_checkpoint(self):
        table = _FlakyChangeTable([{"id": 1}, {"id": 2}], fail_times=1)
        result = copy(table, "retry")
        self.assertEqual(result["retries"], 1)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(table.snapshot_calls, 1)  # 重试复用落盘快照位点
        self.assertTrue(result["verify"]["ok"])

    def test_failure_cleans_target_and_keeps_checkpoint(self):
        table = _FlakyChangeTable([{"id": 1}, {"id": 2}], fail_times=99)
        with self.assertRaises(CopyError):
            copy(table, "fail")
        self.assertEqual(module._target, {})  # 半成品已清理
        checkpoint = self._checkpoint("fail")
        self.assertEqual(checkpoint["snapshot_lsn"], 0)  # 断点位点保留
        self.assertEqual(checkpoint["retries"], 3)  # 重试三次
        self.assertEqual(table.snapshot_calls, 1)  # 重试未重新取快照

    def test_verify_checksum(self):
        copy(Table([{"id": 1}, {"id": 2}]), "sum")
        result = verify()
        self.assertTrue(result["ok"])
        self.assertEqual(result["rows_source"], result["rows_target"])
        self.assertEqual(result["checksum_source"], result["checksum_target"])
        module._target[2] = {"id": 2, "x": 1}  # 篡改内容，行数不变
        result = verify()
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["checksum_source"], result["checksum_target"])
        module._target.clear()
        module._target.update({1: {"id": 1}})  # 行数对不上
        self.assertFalse(verify()["ok"])


if __name__ == "__main__":
    unittest.main()
