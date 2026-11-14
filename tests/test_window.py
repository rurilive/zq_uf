import json
import os
import tempfile
import unittest

from batchwindow import assign, recompute
from batchwindow import (
    configure,
    get_window,
    late_events,
    recompute_records,
    reset,
    watermark,
    window_bounds,
    window_id,
)
from batchwindow import window


class WindowTest(unittest.TestCase):
    def setUp(self):
        window._buckets.clear()

    def test_assign_same_day(self):
        self.assertEqual(assign({"ts": 10})["window"], 0)

    def test_assign_counts(self):
        assign({"ts": 10}); self.assertEqual(assign({"ts": 20})["count"], 2)

    def test_recompute(self):
        assign({"ts": 10})
        self.assertEqual(recompute(0)["count"], 1)


class EventTimeWindowTest(unittest.TestCase):
    """事件时间窗口、迟到重算、幂等、闭区间边界与落盘对账。"""

    SIZE = 10  # 小窗口便于构造跨窗与边界用例

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bw-test-")
        # configure 会从空目录加载空状态；窗口参数在此固定
        configure(state_dir=self.tmp, window_size=self.SIZE, allowed_lateness=0)

    def tearDown(self):
        # 恢复模块默认参数与空状态，避免污染只清 _buckets 的历史测试
        reset()

    # 跨窗：同一批次事件按事件时间各自落窗，不再按自然日拆半
    def test_cross_window_split(self):
        r1 = assign({"id": "a", "ts": 8, "value": 2})
        r2 = assign({"id": "b", "ts": 12, "value": 3})
        self.assertEqual(r1["window"], 0)
        self.assertEqual(r2["window"], 1)
        self.assertEqual(recompute(0)["count"], 1)
        self.assertEqual(recompute(1)["count"], 1)
        self.assertEqual(get_window(0)["sum"], 2)
        self.assertEqual(get_window(1)["sum"], 3)

    # 闭区间边界：边界点归起始于该点的右侧窗口（floor 规则）
    def test_closed_bounds_boundary_assignment(self):
        self.assertEqual(window_id(9), 0)
        self.assertEqual(window_id(10), 1)
        self.assertEqual(window_id(20), 2)
        start, end = window_bounds(1)
        self.assertEqual((start, end), (10, 20))
        assign({"ts": 10})
        meta = get_window(1)
        self.assertEqual(meta["bounds"], "[]")
        self.assertEqual(meta["start"], 10)
        self.assertEqual(meta["end"], 20)
        self.assertIsNone(get_window(0))

    # 迟到事件触发受影响窗口重算，撤销旧值再写新值
    def test_late_event_triggers_recompute(self):
        assign({"id": "a", "ts": 2, "value": 100})
        assign({"id": "b", "ts": 25, "value": 5})  # 推进水位线到 25
        self.assertFalse(late_events())
        self.assertEqual(watermark(), 25)

        result = assign({"id": "late-1", "ts": 3, "value": 7})  # 窗口 0 已关
        self.assertTrue(result["late"])
        self.assertEqual(result["reason"], "late")
        self.assertEqual(result["old_value"], {"count": 1, "sum": 100})
        self.assertEqual(result["new_value"], {"count": 2, "sum": 107})
        self.assertEqual(recompute(0)["count"], 2)

        late = late_events()
        self.assertEqual(len(late), 1)
        self.assertEqual(late[0]["window"], 0)
        self.assertEqual(late[0]["event"]["id"], "late-1")

        records = recompute_records(window=0)
        late_records = [r for r in records if r["reason"] == "late"]
        self.assertEqual(late_records[-1]["old_value"], {"count": 1, "sum": 100})
        self.assertEqual(late_records[-1]["new_value"], {"count": 2, "sum": 107})
        self.assertEqual(late_records[-1]["trigger_event"]["id"], "late-1")

    # 幂等：重复重算不累加，old/new 最终一致
    def test_recompute_idempotent(self):
        assign({"id": "a", "ts": 1, "value": 4})
        assign({"id": "b", "ts": 2, "value": 6})
        first = recompute(0)
        second = recompute(0)
        third = recompute(0)
        for r in (first, second, third):
            self.assertEqual(r["count"], 2)
            self.assertEqual(r["sum"], 10)
        self.assertEqual(second["old_value"], second["new_value"])
        self.assertEqual(third["old_value"], third["new_value"])
        self.assertEqual(get_window(0)["count"], 2)

    # 迟到重算同样幂等：同一迟到事实下重复手动重算结果不变
    def test_late_recompute_idempotent(self):
        assign({"id": "a", "ts": 1})
        assign({"id": "b", "ts": 99})
        assign({"id": "late", "ts": 2})
        r1 = recompute(0)
        r2 = recompute(0)
        self.assertEqual(r1["new_value"], r2["new_value"])
        self.assertEqual(r2["old_value"], r2["new_value"])

    # 带 id 的事件重复投递去重，不二次入窗
    def test_assign_dedup_by_event_id(self):
        assign({"id": "x", "ts": 1})
        again = assign({"id": "x", "ts": 1})
        self.assertTrue(again["duplicate"])
        self.assertEqual(recompute(0)["count"], 1)

    # 重算记录支持按窗口与区间查询
    def test_recompute_records_range_query(self):
        assign({"id": "a", "ts": 1})
        assign({"id": "b", "ts": 11})
        assign({"id": "c", "ts": 22})
        assign({"id": "late-0", "ts": 2})   # 触发窗口 0 重算
        assign({"id": "late-1", "ts": 12})  # 触发窗口 1 重算

        self.assertEqual({r["window"] for r in recompute_records(window=1)}, {1})
        ranged = recompute_records(start_window=0, end_window=1)
        self.assertTrue(ranged)
        self.assertTrue(all(0 <= r["window"] <= 1 for r in ranged))
        self.assertTrue(all(r["window"] != 2 for r in ranged))
        self.assertTrue(all(r["old_value"] is not None for r in ranged))

    # 窗口元数据与审计记录落盘，可对账
    def test_state_persisted_to_disk(self):
        assign({"id": "a", "ts": 2, "value": 9})
        assign({"id": "b", "ts": 33})
        assign({"id": "late", "ts": 3, "value": 1})

        with open(os.path.join(self.tmp, "windows.json"), encoding="utf-8") as f:
            windows_on_disk = json.load(f)
        self.assertEqual(windows_on_disk["0"]["count"], 2)
        self.assertEqual(windows_on_disk["0"]["bounds"], "[]")
        self.assertEqual(windows_on_disk["0"]["start"], 0)
        self.assertEqual(windows_on_disk["0"]["end"], self.SIZE)

        with open(os.path.join(self.tmp, "recompute_records.json"), encoding="utf-8") as f:
            records = json.load(f)
        late_record = records[-1]
        self.assertEqual(late_record["window"], 0)
        self.assertEqual(late_record["old_value"], {"count": 1, "sum": 9})
        self.assertEqual(late_record["new_value"], {"count": 2, "sum": 10})

        with open(os.path.join(self.tmp, "late_events.json"), encoding="utf-8") as f:
            late_on_disk = json.load(f)
        self.assertEqual(late_on_disk[0]["event"]["id"], "late")

        with open(os.path.join(self.tmp, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(meta["window_size"], self.SIZE)
        self.assertEqual(meta["max_ts"], 33)

    # 落盘状态可恢复，恢复后重算仍然幂等
    def test_state_recovery_and_recompute_after_reload(self):
        assign({"id": "a", "ts": 2, "value": 9})
        assign({"id": "b", "ts": 33})
        assign({"id": "late", "ts": 3, "value": 1})
        configure(state_dir=self.tmp, window_size=self.SIZE, allowed_lateness=0)
        self.assertEqual(get_window(0)["count"], 2)
        self.assertEqual(len(late_events()), 1)
        self.assertEqual(len(recompute_records(window=0)), 1)
        again = recompute(0)
        self.assertEqual(again["count"], 2)
        self.assertEqual(again["sum"], 10)
        self.assertEqual(again["old_value"], again["new_value"])

    # 允许迟到宽限：宽限内（水位线尚未严格越过窗口末端）不算迟到；无宽限则迟到
    def test_allowed_lateness_grace(self):
        configure(state_dir=self.tmp, window_size=10, allowed_lateness=5)
        assign({"id": "a", "ts": 20})   # 水位线 15，窗口 1 (end=20) 仍在宽限内
        result = assign({"id": "g", "ts": 15})
        self.assertFalse(result["late"])
        self.assertEqual(late_events(), [])

        reset()
        configure(state_dir=self.tmp, window_size=10, allowed_lateness=0)
        assign({"id": "a2", "ts": 18})  # 落窗口 1，末端 end=20
        assign({"id": "b2", "ts": 22})  # 水位线 22，严格越过窗口 1 末端
        late = assign({"id": "l2", "ts": 12})
        self.assertTrue(late["late"])
        self.assertEqual(late["window"], 1)


if __name__ == "__main__":
    unittest.main()
