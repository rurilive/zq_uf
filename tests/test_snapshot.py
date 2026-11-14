import importlib
import json
import os
import tempfile
import unittest

from cdcsnapshot import SnapshotStore, apply, snapshot

module = importlib.import_module("cdcsnapshot.snapshot")


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        module._rows.clear()

    def test_snapshot_empty(self):
        self.assertEqual(snapshot("t")["rows"], 0)

    def test_apply_counts(self):
        self.assertEqual(apply([{"key": "a", "row": {"v": 1}}])["applied"], 1)

    def test_snapshot_after_apply(self):
        apply([{"key": "a", "row": {"v": 1}}])
        self.assertEqual(snapshot("t")["rows"], 1)


class PositionAlignmentTest(unittest.TestCase):
    """位点对齐、缓存应用、重放幂等和一致性校验。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.position_file = os.path.join(self.tmp.name, "pos.json")
        self.store = module.SnapshotStore(position_file=self.position_file)

    def tearDown(self):
        self.tmp.cleanup()

    def test_position_advances_and_reloads_from_shared_file(self):
        # 位点随事件单调推进，并落共用位点文件
        result = apply_events(
            self.store,
            [
                {"key": "a", "row": {"v": 1}, "pos": 1},
                {"key": "b", "row": {"v": 2}, "pos": 2},
            ],
        )
        self.assertEqual(result["applied"], 2)
        self.assertEqual(self.store.position, 2)
        self.assertTrue(os.path.exists(self.position_file))

        # 新存储从同一文件恢复：位点为 2，行数据来自已有快照
        recovered = SnapshotStore(
            position_file=self.position_file,
            rows={"a": {"v": 1}, "b": {"v": 2}},
        )
        self.assertEqual(recovered.position, 2)

        # 从最后确认位点重放：旧位点跳过，新位点生效
        replay = apply_events(
            recovered,
            [
                {"key": "a", "row": {"v": 1}, "pos": 1},
                {"key": "a", "row": {"v": 9}, "pos": 2},
                {"key": "c", "row": {"v": 3}, "pos": 3},
            ],
        )
        self.assertEqual(replay["applied"], 1)
        self.assertEqual(replay["skipped"], 2)
        self.assertEqual(replay["position"], 3)
        self.assertEqual(recovered.rows["a"], {"v": 1})
        self.assertEqual(recovered.rows["c"], {"v": 3})

    def test_snapshot_records_start_position(self):
        apply_events(self.store, [{"key": "a", "row": {"v": 1}, "pos": 5}])
        start = begin(self.store, "orders")
        self.assertEqual(start, 5)
        snap = commit(self.store)
        self.assertEqual(snap["start_position"], 5)
        self.assertEqual(snap["position"], 5)
        self.assertEqual(snap["rows"], 1)

    def test_changes_during_snapshot_are_cached_then_applied(self):
        apply_events(self.store, [{"key": "a", "row": {"v": 1}, "pos": 1}])
        begin(self.store, "orders")

        # 快照窗口内的变更只缓存，行数据不变
        result = apply_events(
            self.store,
            [
                {"key": "b", "row": {"v": 2}, "pos": 2},
                {"key": "c", "row": {"v": 3}, "pos": 3},
            ],
        )
        self.assertEqual(result["cached"], 2)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(self.store.rows, {"a": {"v": 1}})

        # 提交后缓存补应用，位点推进到最后事件
        snap = commit(self.store)
        self.assertEqual(snap["cached"], 2)
        self.assertEqual(snap["rows"], 3)
        self.assertEqual(snap["position"], 3)
        self.assertEqual(
            self.store.rows,
            {"a": {"v": 1}, "b": {"v": 2}, "c": {"v": 3}},
        )
        with open(self.position_file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["position"], 3)

    def test_replay_is_idempotent(self):
        events = [
            {"key": "a", "row": {"v": 1}, "pos": 1},
            {"key": "b", "row": {"v": 2}, "pos": 2},
        ]
        first = apply_events(self.store, events)
        self.assertEqual(first["applied"], 2)
        # 同样的事件再放一遍，全部跳过，行数和内容不变
        second = apply_events(self.store, list(events))
        self.assertEqual(second["applied"], 0)
        self.assertEqual(second["skipped"], 2)
        self.assertEqual(len(self.store.rows), 2)
        self.assertEqual(self.store.rows["a"], {"v": 1})

    def test_cached_changes_replay_is_idempotent_after_commit(self):
        begin(self.store, "orders")
        apply_events(
            self.store, [{"key": "a", "row": {"v": 1}, "pos": 1}]
        )
        snap = commit(self.store)
        self.assertEqual(snap["position"], 1)
        # 提交后再次重放同一事件，确认位点已推进，直接跳过
        again = apply_events(
            self.store, [{"key": "a", "row": {"v": 1}, "pos": 1}]
        )
        self.assertEqual(again["skipped"], 1)
        self.assertEqual(again["applied"], 0)

    def test_consistency_check_passes_and_reports_diff_rows(self):
        apply_events(
            self.store,
            [
                {"key": "a", "row": {"v": 1}, "pos": 1},
                {"key": "b", "row": {"v": 2}, "pos": 2},
            ],
        )
        snap = snapshot_on(self.store, "orders")
        ok = check_on(
            self.store,
            "orders",
            {
                "rows": snap["check"]["rows"],
                "checksum": snap["check"]["checksum"],
                "data": snap["data"],
            },
        )
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["diff"], [])

        # 期望值与实际不一致：行数与校验和均不符，diff 给出差异行
        bad = check_on(
            self.store,
            "orders",
            {
                "rows": 3,
                "checksum": "0" * 64,
                "data": {
                    "a": {"v": 1},
                    "b": {"v": 99},
                    "c": {"v": 3},
                },
            },
        )
        self.assertFalse(bad["ok"])
        keys = {item["key"]: item for item in bad["diff"]}
        self.assertEqual(keys["b"]["expected"], {"v": 99})
        self.assertEqual(keys["b"]["actual"], {"v": 2})
        self.assertEqual(keys["c"]["expected"], {"v": 3})
        self.assertIsNone(keys["c"]["actual"])

    def test_check_consistency_accepts_snapshot_result_directly(self):
        apply_events(self.store, [{"key": "a", "row": {"v": 1}, "pos": 1}])
        snap = snapshot_on(self.store, "orders")
        # 快照结果本身就可作为校验基线
        result = self.store.check_consistency("orders", snap)
        self.assertTrue(result["ok"])

    def test_legacy_events_without_position_still_apply(self):
        # 不带 pos 的旧事件沿用原行为，不推进位点
        result = apply_events(self.store, [{"key": "a", "row": {"v": 1}}])
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["position"], 0)
        self.assertEqual(self.store.rows["a"], {"v": 1})
        # 位点未推进，不产生位点文件
        self.assertFalse(os.path.exists(self.position_file))

    def test_delete_event_removes_row(self):
        apply_events(
            self.store,
            [
                {"key": "a", "row": {"v": 1}, "pos": 1},
                {"key": "a", "op": "delete", "pos": 2},
            ],
        )
        self.assertNotIn("a", self.store.rows)
        self.assertEqual(self.store.position, 2)

    def test_snapshot_window_lifecycle_guards(self):
        begin(self.store, "orders")
        with self.assertRaises(RuntimeError):
            begin(self.store, "orders")
        commit(self.store)
        with self.assertRaises(RuntimeError):
            commit(self.store)

    def test_position_marker_written_during_snapshot(self):
        # 位点文件存在后，快照窗口开关会同步快照标记
        apply_events(
            self.store, [{"key": "a", "row": {"v": 1}, "pos": 1}]
        )
        begin(self.store, "orders")
        with open(self.position_file, encoding="utf-8") as f:
            state = json.load(f)
        self.assertEqual(state["snapshot"]["table"], "orders")
        self.assertEqual(state["snapshot"]["start_position"], 1)
        commit(self.store)
        with open(self.position_file, encoding="utf-8") as f:
            state = json.load(f)
        self.assertIsNone(state["snapshot"])

    def test_empty_snapshot_does_not_create_position_file(self):
        # 尚无位点时做一次空快照，不产生位点文件
        self.store.snapshot("orders")
        self.assertFalse(os.path.exists(self.position_file))


# 测试辅助：直接走 store 方法，避免依赖模块级全局存储。
def apply_events(store, events):
    return store.apply_events(events)


def begin(store, table):
    return store.begin_snapshot(table)


def commit(store):
    return store.commit_snapshot()


def snapshot_on(store, table):
    return store.snapshot(table)


def check_on(store, table, expected):
    return store.check_consistency(table, expected)
