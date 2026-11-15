"""版本、窗口、落盘与统计的补充测试。

已有的按键去重断言保持不变；这里覆盖新增语义。
"""

import json
import os
import shutil
import tempfile
import unittest

from metricdedup2 import configure, flush, put, reset, stats


def _fresh(window=None):
    """清空内存与落盘配置，回到干净的进程内状态。"""
    configure(state_path=None)
    reset()
    if window is not None:
        configure(window=window)


class VersionTest(unittest.TestCase):
    def setUp(self):
        _fresh(window=60.0)

    def tearDown(self):
        _fresh()

    def test_higher_version_wins(self):
        put({"key": "a", "version": 2, "event_time": 10, "value": 20})
        result = put({"key": "a", "version": 3, "event_time": 11, "value": 30})
        self.assertTrue(result["accepted"])
        self.assertEqual(result["current_version"], 3)
        self.assertEqual(result["size"], 1)

    def test_late_old_version_does_not_overwrite(self):
        put({"key": "a", "version": 5, "event_time": 20, "value": 50})
        result = put({"key": "a", "version": 4, "event_time": 19, "value": 40})
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "stale")
        self.assertEqual(result["dropped"], 1)
        self.assertEqual(result["current_version"], 5)
        self.assertEqual(stats()["dropped"], 1)

    def test_event_time_precedes_version(self):
        # 版本号更大的老事件不应覆盖版本号更小的新事件。
        put({"key": "a", "version": 9, "event_time": 30})
        result = put({"key": "a", "version": 1, "event_time": 31})
        self.assertTrue(result["accepted"])
        self.assertEqual(result["current_version"], 1)

    def test_missing_event_time_falls_back_to_version(self):
        put({"key": "a", "version": 2})
        self.assertFalse(put({"key": "a", "version": 1})["accepted"])
        self.assertTrue(put({"key": "a", "version": 3})["accepted"])

    def test_duplicate_is_idempotent(self):
        metric = {"key": "a", "version": 2, "event_time": 10, "value": 1}
        put(metric)
        result = put(dict(metric))
        self.assertEqual(result["reason"], "duplicate")
        self.assertEqual(result["duplicate"], 1)
        self.assertEqual(result["dropped"], 0)
        self.assertEqual(stats()["duplicates"], 1)
        self.assertEqual(stats()["accepted"], 1)
        self.assertEqual(result["size"], 1)

    def test_stats_by_field(self):
        put({"key": "a", "version": 1, "event_time": 10})
        put({"key": "a", "version": 1, "event_time": 10})
        put({"key": "a", "version": 0, "event_time": 9})
        current = stats()
        self.assertEqual(current["accepted"], 1)
        self.assertEqual(current["duplicates"], 1)
        self.assertEqual(current["dropped"], 1)
        self.assertEqual(current["size"], 1)
        self.assertEqual(current["watermark"], 10)


class WindowTest(unittest.TestCase):
    def setUp(self):
        _fresh(window=10.0)

    def tearDown(self):
        _fresh()

    def test_window_expires_old_entries(self):
        put({"key": "old", "version": 1, "event_time": 100})
        put({"key": "new", "version": 1, "event_time": 120})
        # 水位 120，窗口 10，cutoff 110：old 被清理。
        self.assertEqual(stats()["size"], 1)
        self.assertEqual(stats()["expired"], 1)
        self.assertEqual(stats()["watermark"], 120)

    def test_boundary_is_inclusive(self):
        put({"key": "a", "version": 1, "event_time": 100})
        put({"key": "b", "version": 1, "event_time": 110})
        # event_time == watermark - window 仍然保留。
        self.assertEqual(stats()["size"], 2)

    def test_too_late_is_dropped_not_counted_as_stale(self):
        put({"key": "new", "version": 1, "event_time": 200})
        result = put({"key": "old", "version": 9, "event_time": 150})
        self.assertEqual(result["reason"], "too_late")
        self.assertFalse(result["accepted"])
        self.assertEqual(stats()["too_late"], 1)
        self.assertEqual(stats()["dropped"], 0)

    def test_flush_can_force_watermark(self):
        put({"key": "a", "version": 1, "event_time": 100})
        result = flush(watermark=1000)
        self.assertEqual(result["expired"], 1)
        self.assertEqual(result["size"], 0)
        self.assertEqual(result["watermark"], 1000)

    def test_flush_rejects_backwards_watermark(self):
        put({"key": "a", "version": 1, "event_time": 100})
        with self.assertRaises(ValueError):
            flush(watermark=50)

    def test_window_configurable(self):
        configure(window=5.0)
        put({"key": "a", "version": 1, "event_time": 100})
        put({"key": "b", "version": 1, "event_time": 104})
        self.assertEqual(stats()["size"], 2)
        put({"key": "c", "version": 1, "event_time": 106})
        # 水位 106，cutoff 101：a 被清理。
        self.assertEqual(stats()["size"], 2)
        self.assertEqual(stats()["watermark"], 106)


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mkdtemp(prefix="metricdedup2-")
        _fresh()
        configure(state_path=self.path, window=10.0)

    def tearDown(self):
        configure(state_path=None)
        reset()
        shutil.rmtree(self.path, ignore_errors=True)

    def _restart(self):
        """模拟进程重启：丢掉内存状态，再从磁盘恢复。"""
        configure(state_path=None)
        configure(state_path=self.path)

    def test_journal_recovers_without_flush(self):
        put({"key": "a", "version": 3, "event_time": 100})
        put({"key": "b", "version": 1, "event_time": 105})
        self.assertFalse(os.path.exists(os.path.join(self.path, "snapshot.json")))
        self._restart()
        self.assertEqual(stats()["size"], 2)
        self.assertEqual(stats()["watermark"], 105)
        put({"key": "c", "version": 1, "event_time": 106})
        current = stats()
        self.assertEqual(current["watermark"], 106)
        self.assertEqual(current["accepted"], 3)
        self.assertEqual(current["size"], 3)

    def test_snapshot_plus_journal_restores(self):
        put({"key": "a", "version": 1, "event_time": 100})
        put({"key": "b", "version": 1, "event_time": 105})
        flush()
        put({"key": "c", "version": 1, "event_time": 108})
        self.assertTrue(os.path.exists(os.path.join(self.path, "snapshot.json")))
        self.assertTrue(os.path.exists(os.path.join(self.path, "journal.jsonl")))

        self._restart()
        self.assertEqual(stats()["size"], 3)
        self.assertEqual(stats()["watermark"], 108)

    def test_replay_is_idempotent(self):
        put({"key": "a", "version": 2, "event_time": 100})
        put({"key": "a", "version": 2, "event_time": 100})
        put({"key": "a", "version": 1, "event_time": 99})
        before = stats()
        self._restart()
        after = stats()
        self.assertEqual(after["size"], before["size"])
        self.assertEqual(after["accepted"], before["accepted"])
        self.assertEqual(after["duplicates"], before["duplicates"])
        self.assertEqual(after["dropped"], before["dropped"])
        self.assertEqual(after["watermark"], before["watermark"])

    def test_flush_writes_snapshot_and_clears_journal(self):
        put({"key": "a", "version": 1, "event_time": 100})
        result = flush()
        self.assertTrue(result["persisted"])
        self.assertGreaterEqual(result["flushed"], 1)
        journal = os.path.join(self.path, "journal.jsonl")
        self.assertEqual(os.path.getsize(journal), 0)
        with open(os.path.join(self.path, "snapshot.json"), encoding="utf-8") as handle:
            snapshot = json.load(handle)
        self.assertEqual(snapshot["watermark"], 100)
        self.assertEqual(len(snapshot["entries"]), 1)

    def test_window_survives_restart(self):
        put({"key": "a", "version": 1, "event_time": 100})
        flush()
        self._restart()
        self.assertEqual(stats()["window"], 10.0)

    def test_watermark_continues_after_restore(self):
        put({"key": "a", "version": 1, "event_time": 500})
        flush()
        self._restart()
        result = put({"key": "b", "version": 1, "event_time": 501})
        self.assertTrue(result["accepted"])
        # 水位从 500 继续推进，而不是重置。
        self.assertEqual(stats()["watermark"], 501)


class StateSizeTest(unittest.TestCase):
    def setUp(self):
        _fresh(window=10.0)

    def tearDown(self):
        _fresh()

    def test_size_stays_bounded(self):
        for index in range(200):
            put({"key": f"k{index}", "version": 1, "event_time": float(index)})
        self.assertEqual(stats()["size"], 11)  # 窗口内 11 个点


if __name__ == "__main__":
    unittest.main()
