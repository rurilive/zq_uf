import unittest

from sitemapinc2 import IncrementalIndex, diff, merge


class SmTest(unittest.TestCase):
    def test_new_overwrites(self):
        self.assertEqual(merge({"a": 1}, {"b": 2})["total"], 1)

    def test_deleted_not_counted(self):
        self.assertEqual(merge({"a": 1}, {"b": 2})["deleted"], 0)

    def test_diff_empty(self):
        merge({}, {})
        self.assertEqual(diff()["deleted"], [])


class MergeLastmodTest(unittest.TestCase):
    """lastmod 合并原语：新值覆盖、更旧跳过、相同跳过。"""

    def test_newer_lastmod_overwrites(self):
        stats = merge({"u": 1}, {"u": 2})
        self.assertEqual(stats, {"total": 1, "added": 0, "updated": 1,
                                 "deleted": 0, "skipped": 0})
        self.assertEqual(diff()["updated"], ["u"])

    def test_older_lastmod_skipped(self):
        stats = merge({"u": 5}, {"u": 3})
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(diff()["skipped"], ["u"])

    def test_equal_lastmod_skipped(self):
        stats = merge({"u": 4}, {"u": 4})
        self.assertEqual(stats["added"], 0)
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(diff()["skipped"], ["u"])

    def test_added_urls_reported(self):
        stats = merge({"u": 1}, {"u": 2, "v": 1})
        self.assertEqual(stats["added"], 1)
        self.assertEqual(diff()["added"], ["v"])

    def test_none_arguments_treated_as_empty(self):
        self.assertEqual(merge(None, None)["total"], 0)
        self.assertEqual(diff()["added"], [])


class DeletionPropagationTest(unittest.TestCase):
    """同分片重下后消失的 URL 确认删除、写墓碑并从索引消失。"""

    def test_missing_url_deleted_and_tombstoned(self):
        index = IncrementalIndex()
        index.apply_shard(0, {"https://a/1": 1, "https://a/2": 1})
        stats = index.apply_shard(0, {"https://a/2": 2})
        self.assertEqual(stats["deleted"], 1)
        self.assertEqual(index.diff()["deleted"], ["https://a/1"])
        self.assertNotIn("https://a/1", index.index)
        self.assertIn("https://a/1", index.tombstones())

    def test_tombstone_suppresses_url_from_other_shard(self):
        index = IncrementalIndex()
        index.apply_shard(0, {"u": 1})
        index.apply_shard(1, {"u": 1})
        index.apply_shard(0, {})  # 分片 0 确认删除 u
        self.assertNotIn("u", index.index)
        self.assertIn("u", index.tombstones())

    def test_resurrected_url_clears_tombstone(self):
        index = IncrementalIndex()
        index.apply_shard(0, {"u": 1})
        index.apply_shard(0, {})
        self.assertIn("u", index.tombstones())
        stats = index.apply_shard(0, {"u": 2})
        self.assertNotIn("u", index.tombstones())
        self.assertIn("u", index.index)
        self.assertEqual(stats["added"], 1)
        self.assertEqual(index.index["u"], 2)

    def test_no_delete_on_first_download(self):
        index = IncrementalIndex()
        stats = index.apply_shard(0, {"u": 1})
        self.assertEqual(stats["deleted"], 0)
        self.assertEqual(index.tombstones(), {})


class ShardMergeTest(unittest.TestCase):
    """分片按序号合并：乱序重排、跨分片并集、lastmod 取大。"""

    def test_shards_union_by_seq(self):
        index = IncrementalIndex()
        index.merge_shards({0: {"u": 1}, 1: {"v": 1}})
        self.assertEqual(index.index, {"u": 1, "v": 1})

    def test_out_of_order_shards_merged_by_sequence(self):
        index = IncrementalIndex()
        index.merge_shards([(2, {"w": 1}), (0, {"u": 1}), (1, {"v": 1})])
        self.assertEqual(index.index, {"u": 1, "v": 1, "w": 1})

    def test_cross_shard_same_url_lastmod_wins(self):
        index = IncrementalIndex()
        index.merge_shards([(0, {"u": 5}), (1, {"u": 3})])
        self.assertEqual(index.index["u"], 5)
        index.merge_shards([(1, {"u": 8})])
        self.assertEqual(index.index["u"], 8)

    def test_repeated_shard_apply_is_idempotent(self):
        index = IncrementalIndex()
        first = index.apply_shard(0, {"u": 1, "v": 2})
        snapshot = dict(index.index)
        second = index.apply_shard(0, {"u": 1, "v": 2})
        self.assertEqual(index.index, snapshot)
        self.assertEqual(second["added"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["skipped"], 2)
        self.assertEqual(second["deleted"], 0)
        self.assertEqual(second["total"], first["total"])

    def test_merge_shards_idempotent_on_replay(self):
        shards = [(0, {"u": 1}), (1, {"v": 1, "u": 2})]
        index_a, index_b = IncrementalIndex(), IncrementalIndex()
        index_a.merge_shards(shards)
        index_b.merge_shards(shards)
        index_b.merge_shards(shards)
        self.assertEqual(index_a.index, index_b.index)
        self.assertEqual(index_b.diff()["skipped"].count("u"), 2)
        self.assertEqual(index_b.diff()["deleted"], [])

    def test_incremental_matches_full_rebuild(self):
        index = IncrementalIndex()
        index.merge_shards([(0, {"u": 1}), (1, {"v": 1, "w": 1})])
        index.apply_shard(0, {"u": 3})            # 更新
        index.apply_shard(1, {"v": 1})            # 删除 w
        rebuilt = index.rebuild()
        self.assertEqual(index.index, rebuilt)
        self.assertEqual(rebuilt, {"u": 3, "v": 1})


class TombstoneTtlTest(unittest.TestCase):
    """墓碑保留时间可配，过期清理，None 表示永久。"""

    def test_tombstone_expires_after_ttl(self):
        index = IncrementalIndex(tombstone_ttl=100)
        index.apply_shard(0, {"u": 1}, now=1000)
        index.apply_shard(0, {}, now=1000)
        self.assertIn("u", index.tombstones())
        self.assertEqual(index.cleanup(now=1050), [])
        removed = index.cleanup(now=1100)
        self.assertEqual(removed, ["u"])
        self.assertNotIn("u", index.tombstones())

    def test_none_ttl_keeps_tombstones_forever(self):
        index = IncrementalIndex(tombstone_ttl=None)
        index.apply_shard(0, {"u": 1}, now=0)
        index.apply_shard(0, {}, now=0)
        self.assertEqual(index.cleanup(now=10 ** 12), [])
        self.assertIn("u", index.tombstones())


class ShardFailureTest(unittest.TestCase):
    """分片失败可单独重下，期间保留旧索引。"""

    def test_failed_shard_keeps_old_index(self):
        index = IncrementalIndex()
        index.apply_shard(0, {"u": 1})
        index.apply_shard(1, {"v": 1})
        before = dict(index.index)
        result = index.apply_shard(1, None)
        self.assertEqual(result.get("failed"), 1)
        self.assertEqual(index.index, before)
        self.assertEqual(index.shard(1), {"v": 1})
        retry = index.apply_shard(1, {"v": 2})
        self.assertNotIn("failed", retry)
        self.assertEqual(index.index["v"], 2)


if __name__ == "__main__":
    unittest.main()
