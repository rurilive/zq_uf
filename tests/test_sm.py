import unittest

from sitemapinc2 import DEFAULT_RETENTION_SECONDS, DiffThresholdExceeded, Merger

T0 = 1_000_000


class IncrementalMergeTest(unittest.TestCase):
    def test_first_shard_adds_all(self):
        m = Merger()
        result = m.merge_shard(1, {"a": 1, "b": 2}, now=T0)
        self.assertEqual(result["added"], 2)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["total"], 2)

    def test_new_shard_incremental_not_overwrite(self):
        m = Merger()
        m.merge_shard(1, {"a": 1}, now=T0)
        result = m.merge_shard(2, {"b": 2}, now=T0 + 1)
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["total"], 2)  # 旧索引保留，不再被覆盖

    def test_lastmod_newer_overwrites_older_skipped(self):
        m = Merger()
        m.merge_shard(1, {"a": 10}, now=T0)
        updated = m.merge_shard(1, {"a": 20}, now=T0 + 1)
        self.assertEqual(updated["updated"], 1)
        stale = m.merge_shard(1, {"a": 5}, now=T0 + 2)
        self.assertEqual(stale["skipped"], 1)
        self.assertEqual(stale["updated"], 0)
        self.assertEqual(m.index["a"]["lastmod"], 20)

    def test_identical_redownload_is_idempotent(self):
        m = Merger()
        first = m.merge_shard(1, {"a": 1, "b": 2}, now=T0)
        second = m.merge_shard(1, {"a": 1, "b": 2}, now=T0 + 10)
        self.assertEqual(first["added"], 2)
        self.assertEqual(second["added"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["deleted"], 0)
        self.assertEqual(second["skipped"], 2)
        self.assertEqual(second["total"], 2)
        third = m.merge_shard(1, {"a": 1, "b": 2}, now=T0 + 20)
        self.assertEqual(third["total"], 2)
        self.assertEqual(third["added"], 0)
        # 三次合并后只有两轮新增记录，没有重复结果
        self.assertEqual(m.index["a"]["first_seen"], T0)

    def test_missing_url_gets_tombstone(self):
        m = Merger()
        m.merge_shard(1, {"a": 1, "b": 2}, now=T0)
        result = m.merge_shard(1, {"a": 1}, now=T0 + 10)
        self.assertEqual(result["deleted"], 1)
        self.assertNotIn("b", m.index)
        self.assertIn("b", m.tombstones)
        self.assertEqual(m.tombstones["b"]["deleted_at"], T0 + 10)
        self.assertEqual(
            m.tombstones["b"]["expires_at"], T0 + 10 + DEFAULT_RETENTION_SECONDS
        )

    def test_shards_merged_in_numeric_order(self):
        m = Merger()
        # 乱序传入，内部按序号处理；同 URL 先旧后新
        summary = m.merge_round(
            {2: {"b": 5}, 1: {"a": 1}, 10: {"j": 1}}, now=T0
        )
        self.assertEqual(summary["shards"]["succeeded"], [1, 2, 10])
        self.assertEqual(summary["stats"]["added"], 3)

    def test_failed_shard_isolated_and_retryable(self):
        m = Merger()

        def boom():
            raise ConnectionError("network down")

        summary = m.merge_round(
            {1: {"a": 1}, 2: boom, 3: {"c": 3}}, now=T0
        )
        self.assertEqual(summary["shards"]["failed"][0]["shard"], 2)
        self.assertIn(2, m.failed_shards)
        self.assertEqual(m.index["a"]["lastmod"], 1)
        self.assertEqual(m.index["c"]["lastmod"], 3)
        # 失败不产生任何删除
        self.assertEqual(summary["stats"]["deleted"], 0)

        retry = m.retry_shard(2, {"b": 2}, now=T0 + 10)
        self.assertEqual(retry["added"], 1)
        self.assertNotIn(2, m.failed_shards)

    def test_exception_result_isolated(self):
        m = Merger()
        summary = m.merge_round(
            {1: {"a": 1}, 2: TimeoutError("slow")}, now=T0
        )
        self.assertEqual(summary["shards"]["failed"][0]["error"], "TimeoutError('slow')")
        self.assertEqual(summary["stats"]["added"], 1)

    def test_rollback_restores_tombstone_in_window(self):
        m = Merger(retention_seconds=100)
        m.merge_shard(1, {"a": 1, "b": 2}, now=T0)
        m.merge_shard(1, {"a": 1}, now=T0 + 10)  # b 被删
        record = m.rollback(since=T0 + 5, now=T0 + 20)
        self.assertEqual(record["restored"], ["b"])
        self.assertIn("b", m.index)
        self.assertEqual(m.tombstones["b"]["state"], "rolled_back")
        self.assertNotIn("b", m.deletions())
        self.assertEqual(m.index["b"]["lastmod"], 2)

    def test_rollback_by_url(self):
        m = Merger(retention_seconds=100)
        m.merge_shard(1, {"a": 1, "b": 2, "c": 3}, now=T0)
        m.merge_shard(1, {"a": 1}, now=T0 + 10)
        record = m.rollback(url="c", now=T0 + 20)
        self.assertEqual(record["restored"], ["c"])
        self.assertIn("b", m.tombstones)  # 其余墓碑保留

    def test_rollback_does_not_clobber_newer_data(self):
        m = Merger(retention_seconds=100)
        m.merge_shard(1, {"a": 1}, now=T0)
        m.merge_shard(1, {}, now=T0 + 10)  # a 落墓碑
        m.merge_shard(2, {"a": 99}, now=T0 + 20)  # 复活且 last 更新，墓碑留审计
        self.assertEqual(m.index["a"]["lastmod"], 99)
        self.assertEqual(m.tombstones["a"]["state"], "replaced")
        # 回滚不能用旧的 lastmod=1 覆盖新的 99
        record = m.rollback(since=T0, now=T0 + 30)
        self.assertEqual(record["restored"], [])
        self.assertEqual(record["skipped"], ["a"])
        self.assertEqual(m.index["a"]["lastmod"], 99)

    def test_rollback_state_tombstone_kept_as_audit(self):
        m = Merger(retention_seconds=100)
        m.merge_shard(1, {"a": 1}, now=T0)
        m.merge_shard(1, {}, now=T0 + 10)
        m.rollback(url="a", now=T0 + 20)
        self.assertEqual(m.tombstones["a"]["state"], "rolled_back")
        self.assertNotIn("a", m.deletions())  # 回滚后不再可回滚
        self.assertIn("a", m.index)

    def test_purge_respects_retention(self):
        m = Merger(retention_seconds=100)
        m.merge_shard(1, {"a": 1, "b": 2}, now=T0)
        m.merge_shard(1, {"a": 1}, now=T0)  # b 删除于 T0
        self.assertEqual(m.purge(now=T0 + 99)["purged"], 0)
        self.assertEqual(m.purge(now=T0 + 100)["purged"], 1)
        self.assertEqual(m.deletions(), {})

    def test_diff_review_by_round_and_time(self):
        m = Merger(retention_seconds=1000)
        m.merge_round({1: {"a": 1, "b": 2}}, now=T0)
        m.merge_round({1: {"a": 5}, 2: {"c": 3}}, now=T0 + 100)
        m.merge_round({1: {"a": 5}, 2: {}}, now=T0 + 200)  # c 删除

        d1 = m.diff(round_id=1)
        self.assertEqual(d1["stats"], {
            "added": 2, "updated": 0, "deleted": 0, "skipped": 0
        })
        d2 = m.diff(round_id=2)
        self.assertEqual(d2["stats"]["added"], 1)
        self.assertEqual(d2["stats"]["updated"], 1)
        # 按时间回看：取该时间之前最近完成的轮次
        d_at = m.diff(at=T0 + 150)
        self.assertEqual(d_at["round_id"], 2)
        d3 = m.diff()
        self.assertEqual(d3["round_id"], 3)
        self.assertEqual(d3["stats"]["deleted"], 1)
        self.assertEqual(d3["changes"]["deleted"][0]["url"], "c")

    def test_full_rebuild_matches_incremental(self):
        m = Merger()
        m.merge_round(
            {1: {"a": 1, "b": 2}, 2: {"b": 5, "c": 3}}, now=T0
        )
        report = m.verify_rebuild(threshold=0)
        self.assertEqual(report["diff_count"], 0)
        self.assertEqual(m.index["b"]["lastmod"], 5)

    def test_verify_threshold_blocks_and_confirm_unblocks(self):
        m = Merger(retention_seconds=1000)
        # 阈值 0 正常：完全一致
        m.merge_round({1: {"a": 1}}, now=T0, verify_threshold=0)
        # 人为制造增量索引与分片快照不一致（模拟合并 bug / 数据漂移）
        m.index["ghost"] = {
            "lastmod": 1, "shard": 1, "first_seen": T0, "updated_at": T0
        }
        with self.assertRaises(DiffThresholdExceeded) as ctx:
            m.merge_round({1: {"a": 1, "b": 2}}, now=T0 + 10, verify_threshold=0)
        self.assertEqual(ctx.exception.report["extra"], ["ghost"])
        self.assertEqual(m.blocked_round_id, 2)
        # 阻断状态下拒绝清理墓碑
        with self.assertRaises(RuntimeError):
            m.purge(now=T0 + 100000)
        m.confirm()
        self.assertIsNone(m.blocked_round_id)
        m.purge(now=T0 + 100000)  # 确认后可清理

    def test_verify_threshold_allowance(self):
        m = Merger()
        m.merge_round({1: {"a": 1}}, now=T0)
        m.index["ghost"] = {
            "lastmod": 1, "shard": 1, "first_seen": T0, "updated_at": T0
        }
        # 差异 1 条，阈值 1，放行
        report = m.verify_rebuild(threshold=1)
        self.assertEqual(report["diff_count"], 1)

    def test_url_resurrected_when_reappears(self):
        m = Merger(retention_seconds=100)
        m.merge_shard(1, {"a": 1}, now=T0)
        m.merge_shard(1, {}, now=T0 + 10)
        self.assertIn("a", m.tombstones)
        result = m.merge_shard(1, {"a": 2}, now=T0 + 20)
        self.assertEqual(result["added"], 1)
        self.assertEqual(m.index["a"]["lastmod"], 2)
        self.assertEqual(m.deletions(), {})

    def test_ownership_transfer_not_tombstoned(self):
        m = Merger()
        m.merge_round({1: {"a": 1}, 2: {"b": 2}}, now=T0)
        # a 从分片1 转移到分片2；分片1 不再携带 a
        summary = m.merge_round(
            {1: {}, 2: {"a": 1, "b": 2}}, now=T0 + 10
        )
        self.assertEqual(summary["stats"]["deleted"], 0)
        self.assertIn("a", m.index)
        self.assertEqual(m.index["a"]["shard"], 2)

    def test_empty_shard_retires_its_urls(self):
        m = Merger(retention_seconds=100)
        m.merge_round({1: {"a": 1}, 2: {"b": 2}}, now=T0)
        summary = m.merge_round({1: {}, 2: {"b": 2}}, now=T0 + 10)
        self.assertEqual(summary["stats"]["deleted"], 1)
        self.assertIn("a", m.tombstones)

    def test_iso8601_lastmod_supported(self):
        m = Merger()
        m.merge_shard(1, {"a": "2026-01-01T00:00:00Z"}, now=T0)
        result = m.merge_shard(
            1, {"a": "2026-01-02T00:00:00Z"}, now=T0 + 1
        )
        self.assertEqual(result["updated"], 1)
        stale = m.merge_shard(
            1, {"a": "2026-01-01T00:00:00Z"}, now=T0 + 2
        )
        self.assertEqual(stale["skipped"], 1)

    def test_rollback_history_kept(self):
        m = Merger(retention_seconds=100)
        m.merge_shard(1, {"a": 1}, now=T0)
        m.merge_shard(1, {}, now=T0 + 10)
        m.rollback(since=T0, now=T0 + 20)
        self.assertEqual(len(m.rollbacks), 1)
        self.assertEqual(m.rollbacks[0]["restored"], ["a"])

    def test_unknown_round_and_time_lookup_errors(self):
        m = Merger()
        with self.assertRaises(KeyError):
            m.diff(round_id=99)
        with self.assertRaises(KeyError):
            m.diff(at=T0)

    def test_iterable_entries_input(self):
        m = Merger()
        result = m.merge_shard(1, [("a", 1), ("b", 2)], now=T0)
        self.assertEqual(result["added"], 2)

    def test_failed_shard_keeps_old_snapshot_no_false_deletion(self):
        m = Merger()

        def boom():
            raise ConnectionError("network down")

        m.merge_round({1: {"a": 1, "b": 2}}, now=T0)
        # 分片1 下载失败：旧数据完整保留，没有墓碑、没有删除计数
        summary = m.merge_round({1: boom}, now=T0 + 100)
        self.assertEqual(summary["stats"]["deleted"], 0)
        self.assertEqual(len(m.index), 2)
        self.assertEqual(m.deletions(), {})
        self.assertIn(1, m.failed_shards)
        # 下一轮恢复后仍能正确识别真正的删除
        summary2 = m.merge_round({1: {"a": 1}}, now=T0 + 200, verify_threshold=0)
        self.assertEqual(summary2["stats"]["deleted"], 1)
        self.assertIn("b", m.deletions())

    def test_full_rebuild_accepts_iterable_shards(self):
        m = Merger()
        m.merge_round({1: {"a": 1}}, now=T0)
        rebuilt = m.full_rebuild([(2, {"b": 5}), (1, {"a": 1, "b": 2})])
        # 分片2 的 lastmod 更新，重建取较大值
        self.assertEqual(rebuilt["b"], (5, 2))
        self.assertEqual(rebuilt["a"], (1, 1))


if __name__ == "__main__":
    unittest.main()
