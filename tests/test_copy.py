import unittest

from tablecopy2 import (
    Table,
    ReadOnlyTable,
    CopyError,
    ReadOnlyError,
    copy,
    verify,
    query,
    abort,
    snapshot_of,
    reset,
)


class CopyTest(unittest.TestCase):
    def setUp(self):
        reset()

    # ---- 基础：快照 + 行数（旧接口 dict 兼容） ------------------------
    def test_copy_rows(self):
        result = copy({"name": "a", "rows": [1, 2]}, "b")
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["dst_count"], 2)

    def test_snapshot_is_taken(self):
        # 旧实现 snapshot 为 None；新实现必须返回快照位点
        result = copy({"name": "a", "rows": []}, "c")
        self.assertIsNotNone(result["snapshot"])
        self.assertEqual(result["snapshot_position"], 0)
        self.assertTrue(result["matched"])

    def test_verify_matches(self):
        self.assertTrue(verify()["matched"])

    def test_dict_source_target_queryable_after_copy(self):
        copy({"name": "a", "rows": [{"v": 1}, {"v": 2}]}, "b")
        self.assertEqual(query("b"), [{"v": 1}, {"v": 2}])

    # ---- 先在只读表上验证 ---------------------------------------------
    def test_readonly_table_blocks_writes(self):
        ro = ReadOnlyTable("ro", [{"v": 1}])
        with self.assertRaises(ReadOnlyError):
            ro.insert({"v": 2})
        with self.assertRaises(ReadOnlyError):
            ro.update(1, {"v": 9})
        with self.assertRaises(ReadOnlyError):
            ro.delete(1)

    def test_copy_on_readonly_table_matches_source(self):
        ro = ReadOnlyTable("ro", [{"v": i} for i in range(5)])
        result = copy(ro, "ro_copy")
        self.assertTrue(result["matched"])
        self.assertEqual(result["src_count"], 5)
        self.assertEqual(result["dst_count"], 5)
        self.assertEqual(result["caught_up"], 0)  # 只读表无复制期间变更
        self.assertEqual(result["src_checksum"], result["dst_checksum"])
        self.assertEqual(query("ro_copy"), ro.all_rows())

    # ---- 复制期间写入：快照定版本 + 增量补偿 --------------------------
    def test_concurrent_writes_are_caught_up(self):
        import importlib

        m = importlib.import_module("tablecopy2.copy")
        src = Table("orders", [{"id": "o1"}, {"id": "o2"}])
        calls = {"n": 0}
        real_catchup = m._catchup_phase

        def noisy_catchup(job, fail_at):
            calls["n"] += 1
            if calls["n"] == 1:
                src.insert({"id": "o3"})   # 复制期间新增
                src.update(1, {"id": "o1", "patched": True})
                src.delete(2)              # 复制期间删除
            return real_catchup(job, fail_at)

        m._catchup_phase = noisy_catchup
        try:
            result = copy(src, "orders_copy", batch_size=1)
        finally:
            m._catchup_phase = real_catchup

        self.assertTrue(result["matched"])
        self.assertEqual(result["caught_up"], 3)
        self.assertEqual(result["snapshot_position"], 2)
        self.assertEqual(result["end_position"], src.version)
        self.assertEqual(query("orders_copy"), src.all_rows())
        self.assertEqual(result["src_count"], result["dst_count"])

    # ---- 失败清理半成品，重试从断点继续 --------------------------------
    def test_failure_leaves_no_queryable_half_table(self):
        src = Table("big", [{"n": i} for i in range(6)])
        with self.assertRaises(CopyError):
            copy(src, "big_copy", batch_size=2, fail_at=("bulk", 3))
        with self.assertRaises(CopyError):
            query("big_copy")  # 半成品对外不可查
        # 检查点保留：已完成 3 行
        self.assertEqual(snapshot_of("big_copy")["phase"], "bulk")

    def test_retry_resumes_from_checkpoint(self):
        src = Table("big", [{"n": i} for i in range(6)])
        with self.assertRaises(CopyError):
            copy(src, "big_copy", batch_size=2, fail_at=("bulk", 3))
        result = copy(src, "big_copy", batch_size=2)
        self.assertTrue(result["resumed"])
        self.assertTrue(result["matched"])
        self.assertEqual(result["rows"], 6)
        self.assertEqual(query("big_copy"), src.all_rows())

    def test_catchup_failure_resumes_without_replaying_bulk(self):
        src = Table("t", [{"n": i} for i in range(4)])
        with self.assertRaises(CopyError):
            copy(src, "t_copy", batch_size=2, fail_at=("catchup",))
        src.insert({"n": 99})
        result = copy(src, "t_copy", batch_size=2)
        self.assertTrue(result["resumed"])
        self.assertTrue(result["matched"])
        self.assertEqual(result["caught_up"], 1)
        self.assertEqual(query("t_copy"), src.all_rows())

    def test_abort_cleans_half_finished_copy(self):
        src = Table("t", [{"n": 1}])
        with self.assertRaises(CopyError):
            copy(src, "t_copy", batch_size=1, fail_at=("bulk", 1))
        self.assertTrue(abort("t_copy"))
        with self.assertRaises(CopyError):
            snapshot_of("t_copy")
        # 清理后可重新从头复制
        result = copy(src, "t_copy")
        self.assertFalse(result["resumed"])
        self.assertTrue(result["matched"])

    # ---- 校验不一致：停止并保留两侧，位点可查 --------------------------
    def test_checksum_mismatch_stops_and_retains_both_sides(self):
        src = Table("t", [{"v": 1}, {"v": 2}])
        result = copy(src, "t_copy", tamper_after_publish=True)
        self.assertFalse(result["matched"])
        self.assertEqual(result["status"], "verify_failed")
        self.assertNotEqual(result["src_checksum"], result["dst_checksum"])
        self.assertTrue(result["diff"])  # 差异明细
        kinds = {d["kind"] for d in result["diff"]}
        self.assertIn("content_mismatch", kinds)
        # 两侧数据都保留、目标仍可查
        self.assertEqual(query("t_copy")[0], {"__tampered__": True})
        self.assertEqual(src.all_rows(), [{"v": 1}, {"v": 2}])
        # 快照位点可查
        info = snapshot_of("t_copy")
        self.assertEqual(info["snapshot_position"], result["snapshot_position"])
        # 校验失败后直接重跑被拒绝，必须先处理（abort）
        with self.assertRaises(CopyError):
            copy(src, "t_copy")
        abort("t_copy")
        self.assertTrue(copy(src, "t_copy")["matched"])

    def test_verify_aggregate_reports_failed_job(self):
        good = Table("g", [{"v": 1}])
        bad = Table("b", [{"v": 1}])
        copy(good, "g_copy")
        copy(bad, "b_copy", tamper_after_publish=True)
        summary = verify()
        self.assertFalse(summary["matched"])
        self.assertIn("b_copy", summary["jobs"])
        self.assertFalse(summary["jobs"]["b_copy"]["matched"])
        self.assertTrue(summary["jobs"]["g_copy"]["matched"])

    # ---- 快照定版本：复制期间的变更不影响快照内容 ----------------------
    def test_snapshot_pins_version(self):
        src = Table("t", [{"v": 1}])
        snap = src.snapshot()
        src.insert({"v": 2})
        src.insert({"v": 3})
        # 快照位点的数据仍是当时那 1 行
        self.assertEqual(snap.position, 1)
        self.assertEqual(len(src.state_at(snap.position)), 1)
        self.assertEqual(len(src.state_at(src.version)), 3)


if __name__ == "__main__":
    unittest.main()
