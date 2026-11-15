import json
import os
import shutil
import tempfile
import unittest

from cdcbatch import (
    BatchProcessor,
    BatchTooLarge,
    ChecksumMismatch,
    PositionGap,
    ResumeNotFound,
    apply,
    compute_checksum,
    resume,
)


def _row(pos, table, key, values=None):
    return {"pos": pos, "table": table, "key": key, "values": values or {}}


def _make_batch(batch_id, seq, pos, rows):
    """构造带正确校验和的批次。"""
    batch = {"id": batch_id, "seq": seq, "pos": pos, "rows": rows}
    batch["checksum"] = compute_checksum(batch)
    return batch


class BatchTest(unittest.TestCase):
    def test_apply_counts_rows(self):
        self.assertEqual(apply({"id": "b1", "rows": [{}, {}]})["applied"], 2)

    def test_failure_does_not_roll_back(self):
        result = apply({"id": "b2", "rows": [{}, {"bad": True}]})
        self.assertEqual(result["applied"], 1)
        self.assertFalse(result["rolled_back"])

    def test_resume_replays(self):
        apply({"id": "b3", "rows": [{}]})
        self.assertEqual(resume("b3")["skipped"], 0)


class AtomicBatchTest(unittest.TestCase):
    """新引擎：原子提交、失败反向回滚、幂等、冲突、校验和、续跑。"""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="cdcbatch-test-")
        self.proc = BatchProcessor(self.state_dir, batch_size=10)

    def tearDown(self):
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def _table(self, table):
        path = os.path.join(self.state_dir, "data", "%s.json" % table)
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_atomic_commit_advances_watermark_and_persists(self):
        batch = _make_batch("b1", 1, 0, [
            _row(0, "users", "u1", {"name": "alice"}),
            _row(1, "users", "u2", {"name": "bob"}),
        ])
        result = self.proc.apply(batch)

        self.assertTrue(result["committed"])
        self.assertEqual(result["applied"], 2)
        self.assertEqual(result["watermark"], 1)
        self.assertFalse(result["rolled_back"])
        # 位点与批次号落盘，重新加载引擎后状态仍在
        reloaded = BatchProcessor(self.state_dir)
        self.assertEqual(reloaded.state["watermark"], 1)
        self.assertIn("1", reloaded.state["batches"])
        self.assertEqual(self._table("users")["u1"]["values"], {"name": "alice"})
        # 应用结果写审计
        with open(os.path.join(self.state_dir, "audit.jsonl"), encoding="utf-8") as h:
            events = [json.loads(line) for line in h if line.strip()]
        self.assertEqual(events[-1]["event"], "committed")
        self.assertEqual(events[-1]["applied"], 2)

    def test_failure_rolls_back_in_reverse_order(self):
        batch = _make_batch("b1", 1, 0, [
            _row(0, "users", "u1", {"name": "alice"}),
            _row(1, "orders", "o1", {"amount": 9}),
            {"pos": 2, "table": "users", "key": "u3", "values": {}, "bad": True},
        ])
        result = self.proc.apply(batch)

        self.assertFalse(result["committed"])
        self.assertTrue(result["rolled_back"])
        self.assertEqual(result["rolled_back_rows"], 2)
        # 反向顺序：orders/o1 先撤销，users/u1 后撤销
        self.assertEqual(result["rolled_back_keys"], ["orders/o1", "users/u1"])
        # 已应用行全部回滚，位点不推进
        self.assertEqual(self._table("users"), {})
        self.assertEqual(self._table("orders"), {})
        self.assertIsNone(self.proc.state["watermark"])
        # 失败批次落盘 pending，等待人工续跑
        self.assertTrue(os.path.exists(self.proc.pending_file("b1")))
        # 审计记录回滚事件
        with open(os.path.join(self.state_dir, "audit.jsonl"), encoding="utf-8") as h:
            events = [json.loads(line) for line in h if line.strip()]
        self.assertEqual(events[-1]["event"], "rolled_back")

    def test_duplicate_batch_skipped_by_position(self):
        batch = _make_batch("b1", 1, 0, [
            _row(0, "users", "u1", {"name": "alice"}),
            _row(1, "users", "u2", {"name": "bob"}),
        ])
        first = self.proc.apply(batch)
        second = self.proc.apply(copy_deep(batch))
        third = self.proc.apply(copy_deep(batch))

        self.assertTrue(first["committed"])
        for result in (second, third):
            self.assertTrue(result["committed"])
            self.assertEqual(result["skipped"], 2)
            self.assertEqual(result["applied"], 0)
            self.assertFalse(result["rolled_back"])
        # 数据只写一遍，水位线不重复推进
        self.assertEqual(len(self._table("users")), 2)
        self.assertEqual(self.proc.state["watermark"], 1)

    def test_conflict_keeps_both_sides_and_stats_by_table_field(self):
        first = _make_batch("b1", 1, 0, [
            _row(0, "users", "u1", {"name": "alice", "age": 30}),
        ])
        self.proc.apply(first)
        # 同 key 再次写入且字段值不同；位点连续推进
        second = _make_batch("b2", 2, 1, [
            _row(1, "users", "u1", {"name": "alice2", "age": 31}),
        ])
        result = self.proc.apply(second)

        self.assertTrue(result["committed"])
        self.assertEqual(len(result["conflicts"]), 1)
        conflict = result["conflicts"][0]
        # 两侧原始值都保留
        self.assertEqual(conflict["current"], {"name": "alice", "age": 30})
        self.assertEqual(conflict["incoming"], {"name": "alice2", "age": 31})
        self.assertEqual(conflict["fields"], ["name", "age"])
        # 当前行维持原值，不被覆盖
        self.assertEqual(
            self._table("users")["u1"]["values"], {"name": "alice", "age": 30}
        )
        # 冲突统计按表和字段输出
        self.assertEqual(result["conflict_stats"]["users"]["name"], 1)
        self.assertEqual(result["conflict_stats"]["users"]["age"], 1)
        # 冲突落盘
        with open(os.path.join(self.state_dir, "conflicts.jsonl"), encoding="utf-8") as h:
            persisted = [json.loads(line) for line in h if line.strip()]
        self.assertEqual(persisted[0]["table"], "users")
        self.assertEqual(persisted[0]["current"]["name"], "alice")

    def test_idempotent_same_values_skipped(self):
        first = _make_batch("b1", 1, 0, [
            _row(0, "users", "u1", {"name": "alice"}),
        ])
        self.proc.apply(first)
        second = _make_batch("b2", 2, 1, [
            _row(1, "users", "u1", {"name": "alice"}),
        ])
        result = self.proc.apply(second)
        self.assertTrue(result["committed"])
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["conflicts"], [])

    def test_checksum_mismatch_rejected(self):
        batch = _make_batch("b1", 1, 0, [_row(0, "t", "k", {"v": 1})])
        batch["rows"][0]["values"]["v"] = 2  # 改内容但不重算 checksum
        with self.assertRaises(ChecksumMismatch):
            self.proc.apply(batch)
        self.assertIsNone(self.proc.state["watermark"])
        self.assertEqual(self._table("t"), {})

    def test_duplicate_seq_with_changed_content_rejected(self):
        batch = _make_batch("b1", 1, 0, [_row(0, "t", "k", {"v": 1})])
        self.proc.apply(batch)
        tampered = _make_batch("b1", 1, 0, [_row(0, "t", "k", {"v": 9})])
        with self.assertRaises(ChecksumMismatch):
            self.proc.apply(tampered)

    def test_batch_size_configurable_and_enforced(self):
        proc = BatchProcessor(self.state_dir, batch_size=2)
        ok = _make_batch("b1", 1, 0, [
            _row(0, "t", "k1"), _row(1, "t", "k2"),
        ])
        self.assertTrue(proc.apply(ok)["committed"])

        too_big = _make_batch("b2", 2, 2, [
            _row(2, "t", "k3"),
            _row(3, "t", "k4"),
            _row(4, "t", "k5"),
        ])
        with self.assertRaises(BatchTooLarge):
            proc.apply(too_big)

    def test_position_gap_rejected(self):
        with self.assertRaises(PositionGap):
            self.proc.apply(_make_batch("b1", 1, 5, [_row(5, "t", "k")]))

        self.proc.apply(_make_batch("b1", 1, 0, [_row(0, "t", "k1")]))
        # 跳过位点 1，直接从 2 开始 -> 错位拒绝
        with self.assertRaises(PositionGap):
            self.proc.apply(_make_batch("b2", 2, 2, [_row(2, "t", "k2")]))

    def test_manual_resume_after_failure(self):
        batch = _make_batch("b1", 1, 0, [
            _row(0, "users", "u1", {"name": "alice"}),
            {"pos": 1, "table": "users", "key": "u2", "values": {}, "bad": True},
        ])
        failed = self.proc.apply(batch)
        self.assertTrue(failed["rolled_back"])

        # 人工修复 pending 文件中的坏行后用同一令牌续跑
        pending_path = self.proc.pending_file("b1")
        with open(pending_path, encoding="utf-8") as handle:
            pending = json.load(handle)
        pending["batch"]["rows"][1].pop("bad")
        pending["batch"]["checksum"] = compute_checksum(pending["batch"])
        with open(pending_path, "w", encoding="utf-8") as handle:
            json.dump(pending, handle)

        resumed = self.proc.resume("b1")
        self.assertTrue(resumed["committed"])
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["watermark"], 1)
        self.assertFalse(os.path.exists(pending_path))
        self.assertEqual(self._table("users")["u2"]["values"], {})

    def test_resume_committed_batch_skips_by_position(self):
        batch = _make_batch("b1", 1, 0, [
            _row(0, "users", "u1", {"name": "alice"}),
        ])
        self.proc.apply(batch)
        result = self.proc.resume("b1")
        self.assertTrue(result["resumed"])
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["applied"], 0)
        self.assertTrue(result["committed"])

    def test_resume_unknown_token_raises(self):
        with self.assertRaises(ResumeNotFound):
            self.proc.resume("nope")

    def test_rolled_back_then_fixed_batch_can_reapply(self):
        batch = _make_batch("b1", 1, 0, [
            _row(0, "t", "k1", {"v": 1}),
            {"pos": 1, "table": "t", "key": "k2", "values": {}, "bad": True},
        ])
        result = self.proc.apply(batch)
        self.assertTrue(result["rolled_back"])
        # 位点未推进，同批次号修复后可重放
        fixed = _make_batch("b1", 1, 0, [
            _row(0, "t", "k1", {"v": 1}),
            _row(1, "t", "k2", {"v": 2}),
        ])
        again = self.proc.apply(fixed)
        self.assertTrue(again["committed"])
        self.assertEqual(again["applied"], 2)
        self.assertEqual(self.proc.state["watermark"], 1)


def copy_deep(value):
    return json.loads(json.dumps(value))


if __name__ == "__main__":
    unittest.main()
