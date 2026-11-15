import json
import os
import tempfile
import unittest

from coldhot import (
    TierManager,
    ColdTierError,
    ColdTierUnavailable,
    checksum_of,
    reset,
    seed,
    archive,
    query,
    restore,
    confirm_cleanup,
    list_archives,
    export_restores,
)


def orders():
    return [
        {"order_id": 1, "ts": 1, "status": "paid", "amount": 100},
        {"order_id": 2, "ts": 2, "status": "paid", "amount": 200},
        {"order_id": 3, "ts": 3, "status": "unpaid", "amount": 300},
        {"order_id": 4, "ts": 4, "status": "paid", "amount": 400},
        {"order_id": 5, "ts": 5, "status": "paid", "amount": 500},
    ]


class TierManagerTest(unittest.TestCase):
    def setUp(self):
        self.mgr = TierManager()
        self.mgr.seed("orders", orders())

    # ------------------------------------------------------------------
    # 归档
    # ------------------------------------------------------------------
    def test_archive_moves_rows_and_records_location_range(self):
        result = self.mgr.archive("orders", {"start": 1, "end": 3})
        self.assertEqual(result["archived"], 2)
        self.assertIsNotNone(result["location"])
        self.assertEqual(result["range"], {"start": 1, "end": 3})
        self.assertEqual(result["status"], "archived")

        # 在线表只剩 ts>=3 的数据
        online_ts = [row["ts"] for row in self.mgr.online["orders"]]
        self.assertEqual(online_ts, [3, 4, 5])

        # 归档记录可查询，范围与位置都在账上
        records = self.mgr.list_archives("orders")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["row_count"], 2)
        self.assertEqual(records[0]["checksum"], checksum_of(orders()[:2]))

    def test_archive_empty_range_keeps_everything(self):
        result = self.mgr.archive("orders", {"start": 100, "end": 200})
        self.assertEqual(result["archived"], 0)
        self.assertIsNone(result["location"])
        self.assertEqual(len(self.mgr.online["orders"]), 5)

    # ------------------------------------------------------------------
    # 透明查询与合并
    # ------------------------------------------------------------------
    def test_transparent_query_merges_online_and_cold(self):
        self.mgr.archive("orders", {"start": 1, "end": 3})  # 搬走 ts 1,2

        result = self.mgr.query("orders", {"status": "paid"})
        # 在线 3 条（paid: ts 4,5；unpaid: ts3 被过滤）+ 冷层 2 条
        self.assertEqual(result["routed"], "online+cold")
        self.assertEqual(result["tiers"], ["online", "cold"])
        self.assertEqual(result["sources"], {"online": 2, "cold": 2})
        self.assertEqual(result["count"], 4)
        self.assertEqual([row["order_id"] for row in result["rows"]], [1, 2, 4, 5])
        self.assertTrue(result["complete"])
        self.assertEqual(result["warnings"], [])

    def test_query_with_time_window_routes_only_overlapping_shard(self):
        self.mgr.archive("orders", {"start": 1, "end": 3})

        # 时间窗与归档范围无重叠 -> 只查在线
        online_only = self.mgr.query("orders", {"start": 3, "end": 6})
        self.assertEqual(online_only["routed"], "online")
        self.assertEqual(online_only["tiers"], ["online"])
        self.assertEqual(
            [row["order_id"] for row in online_only["rows"]], [3, 4, 5]
        )

        # 时间窗跨冷热边界 -> 合并
        crossed = self.mgr.query("orders", {"start": 2, "end": 5})
        self.assertEqual(crossed["routed"], "online+cold")
        self.assertEqual(
            [row["order_id"] for row in crossed["rows"]], [2, 3, 4]
        )

    def test_query_before_archive_is_online_only(self):
        result = self.mgr.query("orders", {})
        self.assertEqual(result["routed"], "online")
        self.assertEqual(result["count"], 5)

    # ------------------------------------------------------------------
    # 冷层不可用：提示而非空结果
    # ------------------------------------------------------------------
    def test_cold_unavailable_warns_instead_of_returning_empty(self):
        self.mgr.archive("orders", {"start": 1, "end": 3})
        self.mgr.cold.available = False

        result = self.mgr.query("orders", {"status": "paid"})
        self.assertTrue(result["degraded"])
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(result["warnings"][0]["code"], "COLD_TIER_UNAVAILABLE")
        # 在线数据照常返回（paid 的 ts4,5），绝不是空结果
        self.assertEqual(result["sources"]["online"], 2)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["tiers"], ["online"])

        # 冷层恢复后查询重新完整
        self.mgr.cold.available = True
        healed = self.mgr.query("orders", {"status": "paid"})
        self.assertFalse(healed["degraded"])
        self.assertEqual(healed["count"], 4)

    # ------------------------------------------------------------------
    # 恢复：行数 + 内容校验
    # ------------------------------------------------------------------
    def test_restore_verifies_rows_and_keeps_cold_copy(self):
        arc = self.mgr.archive("orders", {"start": 1, "end": 3})

        result = self.mgr.restore(archive_id=arc["archive_id"])
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["status"], "restored")
        self.assertEqual(result["restored_rows"], 2)
        self.assertTrue(result["verification"]["row_count_ok"])
        self.assertTrue(result["verification"]["checksum_ok"])

        # 在线表数据完整回来，且查询无重复
        self.assertEqual(len(self.mgr.online["orders"]), 5)
        merged = self.mgr.query("orders", {})
        self.assertEqual(merged["count"], 5)

        # 冷层副本仍在，需人工确认才清理
        record = self.mgr.list_archives("orders")[0]
        self.assertEqual(record["status"], "restored")
        self.assertTrue(self.mgr.cold.contains(record["location"]))

    def test_restore_is_idempotent(self):
        arc = self.mgr.archive("orders", {"start": 1, "end": 3})
        first = self.mgr.restore(archive_id=arc["archive_id"])
        second = self.mgr.restore(archive_id=arc["archive_id"])

        self.assertEqual(first["restored_rows"], 2)
        self.assertTrue(second["verification"].get("row_count_ok", True))
        self.assertEqual(len(self.mgr.online["orders"]), 5)
        # 每次尝试都留痕
        self.assertEqual(len(self.mgr.restore_log), 2)

    def test_restore_failure_keeps_archive_copy_and_is_retryable(self):
        arc = self.mgr.archive("orders", {"start": 1, "end": 3})
        self.mgr.cold.corrupt_shard(arc["location"])  # 内容损坏

        failed = self.mgr.restore(archive_id=arc["archive_id"])
        self.assertEqual(failed["result"], "failed")
        self.assertEqual(failed["status"], "restore_failed")
        self.assertFalse(failed["verification"]["checksum_ok"])
        # 在线数据未被污染
        self.assertEqual(len(self.mgr.online["orders"]), 3)
        # 冷层副本保留
        self.assertTrue(self.mgr.cold.contains(arc["location"]))
        # 未通过校验不允许清理
        with self.assertRaises(ColdTierError):
            self.mgr.confirm_cleanup(archive_id=arc["archive_id"])

        # 修复冷层后可重试，重试成功
        self.mgr.cold.available = True
        original = [
            {"order_id": 1, "ts": 1, "status": "paid", "amount": 100},
            {"order_id": 2, "ts": 2, "status": "paid", "amount": 200},
        ]
        self.mgr.cold.put(arc["location"], original)
        retried = self.mgr.restore(archive_id=arc["archive_id"])
        self.assertEqual(retried["result"], "success")
        self.assertEqual(retried["status"], "restored")

    def test_restore_when_cold_unavailable_fails_and_retryable(self):
        arc = self.mgr.archive("orders", {"start": 1, "end": 3})
        self.mgr.cold.available = False

        failed = self.mgr.restore(archive_id=arc["archive_id"])
        self.assertEqual(failed["result"], "failed")
        self.assertEqual(failed["status"], "restore_failed")
        self.assertEqual(failed["restored_rows"], 0)

        self.mgr.cold.available = True
        retried = self.mgr.restore(archive_id=arc["archive_id"])
        self.assertEqual(retried["result"], "success")

    # ------------------------------------------------------------------
    # 人工确认清理
    # ------------------------------------------------------------------
    def test_confirm_cleanup_requires_successful_restore(self):
        arc = self.mgr.archive("orders", {"start": 1, "end": 3})
        # 尚未恢复，拒绝清理
        with self.assertRaises(ColdTierError):
            self.mgr.confirm_cleanup(archive_id=arc["archive_id"])

        self.mgr.restore(archive_id=arc["archive_id"])
        cleanup = self.mgr.confirm_cleanup(archive_id=arc["archive_id"])
        self.assertEqual(cleanup["status"], "cleaned")
        self.assertFalse(self.mgr.cold.contains(arc["location"]))
        # 在线数据不受影响
        self.assertEqual(len(self.mgr.online["orders"]), 5)

    # ------------------------------------------------------------------
    # 恢复记录导出与回看
    # ------------------------------------------------------------------
    def test_export_restores_to_file_and_memory(self):
        arc = self.mgr.archive("orders", {"start": 1, "end": 3})
        self.mgr.restore(archive_id=arc["archive_id"])

        entries = self.mgr.export_restores("orders")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["result"], "success")
        self.assertEqual(entries[0]["archive_id"], arc["archive_id"])

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "restores.json")
            returned = self.mgr.export_restores(path=path)
            with open(path, encoding="utf-8") as handle:
                on_disk = json.load(handle)
            self.assertEqual(on_disk, returned)

    def test_archive_and_restore_logs_are_separate(self):
        arc = self.mgr.archive("orders", {"start": 1, "end": 3})
        self.mgr.restore(archive_id=arc["archive_id"])
        self.assertEqual(len(self.mgr.archive_log), 1)
        self.assertEqual(len(self.mgr.restore_log), 1)
        self.assertNotIn("result", self.mgr.archive_log[0])


class ModuleLevelApiTest(unittest.TestCase):
    """demo.py 使用的模块级函数式 API 冒烟测试。"""

    def setUp(self):
        reset()

    def test_functional_api_flow(self):
        seed("orders", orders())
        arc = archive("orders", {"start": 1, "end": 3})
        self.assertEqual(arc["archived"], 2)
        self.assertIsNotNone(arc["location"])

        result = query("orders", {"status": "paid"})
        self.assertEqual(result["routed"], "online+cold")
        self.assertEqual(result["count"], 4)

        restored = restore(archive_id=arc["archive_id"])
        self.assertEqual(restored["result"], "success")
        self.assertEqual(len(list_archives("orders")), 1)


if __name__ == "__main__":
    unittest.main()
